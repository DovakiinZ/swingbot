"""
data/websocket_monitor.py — Real-time price monitoring via WebSocket.

Connects to MEXC WebSocket and streams live price ticks.
Runs in a background thread alongside the main bot loop.
Feeds price data to the MomentumDetector for instant signal generation.

MEXC WebSocket docs: https://mexcdevelop.github.io/apidocs/spot_v3_en/
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
    bid:       float = 0.0
    ask:       float = 0.0


class WebSocketMonitor:
    """
    Monitors real-time prices for multiple symbols via MEXC WebSocket.
    Maintains a rolling price history for momentum calculation.
    Calls callback function when momentum spike detected.
    """

    MEXC_WS_URL = "wss://wbs.mexc.com/ws"
    RECONNECT_DELAY = 5    # seconds before reconnect attempt
    HISTORY_SIZE    = 60   # keep last 60 ticks per symbol (~1 minute)

    def __init__(
        self,
        symbols: list,
        on_momentum: Callable,
        momentum_threshold: float = 0.003,   # 0.3% move = momentum signal
        volume_multiplier: float = 2.0       # Volume must be 2x average
    ):
        self.symbols            = [s.replace('/', '') for s in symbols]
        self.symbol_map         = {s.replace('/', ''): s for s in symbols}  # BTCUSDT -> BTC/USDT
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
        self._last_ping = time.time()
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
            self.MEXC_WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        # MEXC handles ping/pong at application level (text "ping"/"pong"),
        # not at WebSocket protocol level — disable library-level pings
        self.ws.run_forever(ping_interval=0)

    def _on_open(self, ws) -> None:
        """Subscribe to deals (real-time trades) for all monitored symbols."""
        logger.warning(f"[WS] Connected — subscribing to {len(self.symbols)} symbols")
        self._connected = True

        # Batch all subscriptions into one message (MEXC requirement)
        params = [f"spot@public.deals.v3.api@{symbol}" for symbol in self.symbols]
        subscribe_msg = {
            "method": "SUBSCRIPTION",
            "params": params
        }
        ws.send(json.dumps(subscribe_msg))
        self._last_ping = time.time()

    def _on_message(self, ws, message: str) -> None:
        """Process incoming price tick from MEXC deals stream."""
        try:
            # MEXC sends ping as text "ping" — respond with "pong"
            if message == "ping":
                ws.send("pong")
                self._last_ping = time.time()
                return

            data = json.loads(message)

            # Handle subscription confirmations and other system messages
            if data.get('id') is not None or data.get('code') is not None:
                if data.get('code') == 0:
                    logger.debug(f"[WS] Subscription confirmed")
                return

            # Extract channel info
            channel = data.get('c', '')  # e.g. "spot@public.deals.v3.api@BTCUSDT"
            d = data.get('d', {})

            if not channel or not d:
                return

            # Parse symbol from channel name
            # Channel format: spot@public.deals.v3.api@BTCUSDT
            parts = channel.split('@')
            if len(parts) < 3:
                return
            symbol = parts[-1]  # e.g. "BTCUSDT"

            # Deals data: d.deals is a list of trades
            # Each deal: {"p": price, "v": volume, "S": side (1=buy,2=sell), "t": timestamp}
            deals = d.get('deals', [])
            if not deals:
                return

            # Use the most recent deal
            latest = deals[-1]
            price = float(latest.get('p', 0) or 0)
            volume = float(latest.get('v', 0) or 0)

            if price <= 0:
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

        Conditions for momentum BUY signal:
        1. Price moved > momentum_threshold in last 30 seconds
        2. Move is in one direction (not oscillating)
        3. Volume is above average (surge confirmation)

        Conditions for momentum SELL signal:
        Same but downward.
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

        # Volume average
        avg_volume = sum(t.volume for t in ticks) / len(ticks)
        current_volume = current.volume
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

        # Check thresholds
        abs_change = abs(price_change)
        if abs_change >= self.momentum_threshold and volume_ratio >= self.volume_multiplier:
            direction = "BUY" if price_change > 0 else "SELL"

            # Map back to slash format
            original_symbol = self.symbol_map.get(symbol, symbol.replace('USDT', '/USDT'))

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
        symbol_key = symbol.replace('/', '')
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
        new_keys = [s.replace('/', '') for s in new_symbols]
        # Add any new symbols to history tracking
        for key in new_keys:
            if key not in self.price_history:
                self.price_history[key] = deque(maxlen=self.HISTORY_SIZE)
        # Update symbol map
        for s in new_symbols:
            self.symbol_map[s.replace('/', '')] = s
        self.symbols = new_keys
