"""
data/websocket_monitor.py — Real-time price monitoring via WebSocket.

Connects to Binance public WebSocket (no auth needed) and streams
live aggregated trades. Runs in a background thread alongside the
main bot loop. Detects momentum spikes for instant signal generation.

Note: Binance WebSocket is used as a READ-ONLY data source.
Trading still happens on MEXC via the broker.
"""

import json
import time
import logging
import threading
from typing import Callable, Optional
from dataclasses import dataclass
from collections import deque

logger = logging.getLogger(__name__)

try:
    import websocket
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False
    logger.warning("[WS] websocket-client not installed — WebSocket disabled. pip install websocket-client")


@dataclass
class PriceTick:
    """Single price tick from WebSocket stream."""
    symbol:    str
    price:     float
    volume:    float
    timestamp: float   # Unix timestamp in seconds


class WebSocketMonitor:
    """
    Monitors real-time prices for multiple symbols via Binance public WebSocket.
    Maintains a rolling price history for momentum calculation.
    Calls callback function when momentum spike detected.
    """

    BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"
    RECONNECT_DELAY = 5    # seconds before reconnect attempt
    HISTORY_SIZE    = 120  # keep last 120 ticks per symbol

    def __init__(
        self,
        symbols: list,
        on_momentum: Callable,
        momentum_threshold: float = 0.003,   # 0.3% move = momentum signal
        volume_multiplier: float = 2.0       # Volume must be 2x average
    ):
        # Binance uses lowercase: btcusdt
        self.symbols            = [s.replace('/', '').lower() for s in symbols]
        # Map lowercase back to original format: btcusdt -> BTC/USDT
        self.symbol_map         = {s.replace('/', '').lower(): s for s in symbols}
        self.on_momentum        = on_momentum
        self.momentum_threshold = momentum_threshold
        self.volume_multiplier  = volume_multiplier

        # Price history per symbol: deque of PriceTick
        self.price_history: dict = {
            s: deque(maxlen=self.HISTORY_SIZE) for s in self.symbols
        }

        self.ws = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._momentum_signals_today = 0
        self._last_momentum_info = ""
        self._connected = False

    def start(self) -> None:
        """Start WebSocket in background thread."""
        if not WS_AVAILABLE:
            logger.warning("[WS] Cannot start — websocket-client not installed")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_forever,
            daemon=True,
            name="websocket-monitor"
        )
        self._thread.start()
        logger.warning("[WS] WebSocket monitor started")

    def stop(self) -> None:
        """Stop WebSocket monitor."""
        self._running = False
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self._connected = False
        logger.warning("[WS] WebSocket monitor stopped")

    def _run_forever(self) -> None:
        """Keep WebSocket alive — reconnect on disconnect."""
        while self._running:
            try:
                self._connect()
            except Exception as e:
                logger.error(f"[WS] Connection error: {e}")
            self._connected = False
            if self._running:
                logger.warning(f"[WS] Reconnecting in {self.RECONNECT_DELAY}s...")
                time.sleep(self.RECONNECT_DELAY)

    def _connect(self) -> None:
        """Create and run WebSocket connection."""
        self.ws = websocket.WebSocketApp(
            self.BINANCE_WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self.ws.run_forever(ping_interval=20, ping_timeout=10)

    def _on_open(self, ws) -> None:
        """Subscribe to aggTrade streams for all monitored symbols."""
        logger.warning(f"[WS] Connected to Binance — subscribing to {len(self.symbols)} symbols")
        self._connected = True

        # Binance: subscribe to aggregated trades for each symbol
        params = [f"{symbol}@aggTrade" for symbol in self.symbols]
        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": params,
            "id": 1
        }
        ws.send(json.dumps(subscribe_msg))

    def _on_message(self, ws, message: str) -> None:
        """Process incoming aggregated trade from Binance."""
        try:
            data = json.loads(message)

            # Skip subscription confirmation
            if 'result' in data or 'id' in data:
                return

            # Binance aggTrade format:
            # {"e":"aggTrade","s":"BTCUSDT","p":"66602.43","q":"0.00067","T":1774768622063,...}
            event_type = data.get('e', '')
            if event_type != 'aggTrade':
                return

            symbol = data.get('s', '').lower()  # "BTCUSDT" -> "btcusdt"
            price  = float(data.get('p', 0) or 0)
            volume = float(data.get('q', 0) or 0)

            if price <= 0 or not symbol:
                return

            tick = PriceTick(
                symbol=symbol,
                price=price,
                volume=volume,
                timestamp=time.time(),
            )

            # Store in history
            history = self.price_history.get(symbol)
            if history is not None:
                history.append(tick)
                # Check for momentum after we have enough history
                if len(history) >= 10:
                    self._check_momentum(symbol, tick, history)

        except Exception as e:
            logger.debug(f"[WS] Message parse error: {e}")

    def _check_momentum(
        self,
        symbol: str,
        current: PriceTick,
        history: deque
    ) -> None:
        """
        Detect momentum spike — the core signal.

        Conditions for momentum signal:
        1. Price moved > momentum_threshold in last 30 seconds
        2. Volume is above average (surge confirmation)
        """
        ticks = list(history)

        # Get price 30 seconds ago
        now = current.timestamp
        ticks_30s = [t for t in ticks if now - t.timestamp <= 30]
        if len(ticks_30s) < 5:
            return

        price_30s_ago = ticks_30s[0].price
        current_price = current.price

        if price_30s_ago <= 0:
            return

        # Price change in last 30 seconds
        price_change = (current_price - price_30s_ago) / price_30s_ago

        # Volume: sum volume over last 30s vs average
        vol_30s = sum(t.volume for t in ticks_30s)
        older_ticks = [t for t in ticks if now - t.timestamp > 30]
        if len(older_ticks) >= 5:
            avg_vol_30s = sum(t.volume for t in older_ticks) / len(older_ticks) * len(ticks_30s)
            volume_ratio = vol_30s / avg_vol_30s if avg_vol_30s > 0 else 1.0
        else:
            volume_ratio = 1.0

        # Check thresholds
        abs_change = abs(price_change)
        if abs_change >= self.momentum_threshold and volume_ratio >= self.volume_multiplier:
            direction = "BUY" if price_change > 0 else "SELL"

            # Map back to original symbol format (e.g. btcusdt -> BTC/USDT)
            original_symbol = self.symbol_map.get(symbol, symbol.upper().replace('USDT', '/USDT'))

            logger.warning(
                f"[WS MOMENTUM] {original_symbol} | {direction} | "
                f"change={price_change:+.3%} | volume={volume_ratio:.1f}x"
            )

            self._momentum_signals_today += 1
            self._last_momentum_info = (
                f"{original_symbol} {price_change:+.2%} {volume_ratio:.1f}x volume"
            )

            # Fire callback
            try:
                self.on_momentum(
                    symbol=original_symbol,
                    direction=direction,
                    price=current_price,
                    price_change_pct=price_change,
                    volume_ratio=volume_ratio,
                    timestamp=now
                )
            except Exception as e:
                logger.error(f"[WS] Momentum callback error: {e}")

    def _on_error(self, ws, error) -> None:
        logger.error(f"[WS] Error: {error}")

    def _on_close(self, ws, close_status, close_msg) -> None:
        self._connected = False
        logger.warning(f"[WS] Connection closed: {close_status}")

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Get most recent price for a symbol."""
        symbol_key = symbol.replace('/', '').lower()
        history = self.price_history.get(symbol_key)
        if history and len(history) > 0:
            return list(history)[-1].price
        return None

    def is_connected(self) -> bool:
        """Check if WebSocket is currently connected."""
        return self._running and self._connected and self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict:
        """Return WebSocket status for dashboard."""
        return {
            'connected': self.is_connected(),
            'symbols_monitored': len(self.symbols),
            'momentum_signals_today': self._momentum_signals_today,
            'last_momentum': self._last_momentum_info or "—",
        }

    def update_symbols(self, new_symbols: list) -> None:
        """Update monitored symbols list (called after scan)."""
        new_keys = [s.replace('/', '').lower() for s in new_symbols]
        for key in new_keys:
            if key not in self.price_history:
                self.price_history[key] = deque(maxlen=self.HISTORY_SIZE)
        for s in new_symbols:
            self.symbol_map[s.replace('/', '').lower()] = s
        self.symbols = new_keys
