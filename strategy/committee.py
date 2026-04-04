"""
Trading Committee — 5 independent agents vote on every trade decision.

Agents:
  TrendAgent     (0.25) — EMA, ADX, price structure
  MomentumAgent  (0.20) — RSI, MACD, volume
  SentimentAgent (0.25) — 4 external sentiment sources
  RiskAgent      (0.20) — drawdown, position size, daily P&L
  PatternAgent   (0.10) — candlestick patterns (engulfing, doji, hammer)

Voting:
  Each agent votes BUY / SELL / HOLD with confidence 0.0-1.0.
  Weighted scores determine final decision:
    weighted_buy  >= 0.60 → BUY
    weighted_sell >= 0.60 → SELL
    else                  → HOLD

Gate check before execution:
  1. Committee = BUY
  2. signal_score >= 70
  3. Sentiment != BEARISH
  4. Regime = TRENDING_UP
  5. Daily P&L not at -$15 limit
  6. No duplicate position open
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

from strategy.regimes import MarketRegime

logger = logging.getLogger(__name__)


@dataclass
class AgentVote:
    """A single agent's vote."""
    agent: str
    decision: str        # BUY, SELL, HOLD
    confidence: float    # 0.0 to 1.0
    reason: str


class TrendAgent:
    """Focuses on EMA alignment, ADX strength, and price structure."""
    NAME = "TrendAgent"

    def vote(self, df: pd.DataFrame, regime: MarketRegime, **kwargs) -> AgentVote:
        if df is None or df.empty or len(df) < 5:
            return AgentVote(self.NAME, "HOLD", 0.3, "insufficient data")

        curr = df.iloc[-1]
        ema_fast = curr.get('ema_fast', 0)
        ema_slow = curr.get('ema_slow', 0)
        adx = curr.get('adx', 0)
        close = curr.get('close', 0)

        if pd.isna(adx):
            adx = 0

        # Strong uptrend: fast > slow, high ADX, price above both EMAs
        if ema_fast > ema_slow and close > ema_slow:
            if adx >= 30:
                return AgentVote(self.NAME, "BUY", 0.90, f"strong uptrend ADX={adx:.0f}")
            elif adx >= 20:
                return AgentVote(self.NAME, "BUY", 0.70, f"uptrend ADX={adx:.0f}")
            else:
                return AgentVote(self.NAME, "HOLD", 0.50, f"weak trend ADX={adx:.0f}")

        # Strong downtrend
        elif ema_fast < ema_slow and close < ema_slow:
            if adx >= 30:
                return AgentVote(self.NAME, "SELL", 0.90, f"strong downtrend ADX={adx:.0f}")
            elif adx >= 20:
                return AgentVote(self.NAME, "SELL", 0.70, f"downtrend ADX={adx:.0f}")
            else:
                return AgentVote(self.NAME, "HOLD", 0.50, f"weak trend ADX={adx:.0f}")

        return AgentVote(self.NAME, "HOLD", 0.40, "no clear trend")


class MomentumAgent:
    """Focuses on RSI, MACD histogram, and volume."""
    NAME = "MomentumAgent"

    def vote(self, df: pd.DataFrame, regime: MarketRegime, **kwargs) -> AgentVote:
        if df is None or df.empty or len(df) < 5:
            return AgentVote(self.NAME, "HOLD", 0.3, "insufficient data")

        curr = df.iloc[-1]
        rsi = curr.get('rsi', 50)
        macd_hist = curr.get('macd_hist', 0)
        vol_ratio = curr.get('volume_ratio', 1.0)

        if pd.isna(rsi):
            rsi = 50
        if pd.isna(macd_hist):
            macd_hist = 0
        if pd.isna(vol_ratio):
            vol_ratio = 1.0

        signals = 0  # positive = bullish, negative = bearish
        conf_boost = 0.0

        # RSI
        if rsi < 35:
            signals += 2  # deeply oversold — strong buy
        elif rsi < 45:
            signals += 1
        elif rsi > 65:
            signals -= 2  # deeply overbought — strong sell
        elif rsi > 55:
            signals -= 1

        # MACD histogram direction
        if macd_hist > 0:
            signals += 1
        elif macd_hist < 0:
            signals -= 1

        # Volume confirmation
        if vol_ratio > 1.5:
            conf_boost = 0.15

        if signals >= 2:
            conf = min(0.95, 0.65 + conf_boost)
            return AgentVote(self.NAME, "BUY", conf, f"RSI={rsi:.0f} MACD_H={macd_hist:.4f} vol={vol_ratio:.1f}x")
        elif signals <= -2:
            conf = min(0.95, 0.65 + conf_boost)
            return AgentVote(self.NAME, "SELL", conf, f"RSI={rsi:.0f} MACD_H={macd_hist:.4f}")
        else:
            return AgentVote(self.NAME, "HOLD", 0.50, f"mixed RSI={rsi:.0f}")


class SentimentAgent:
    """Uses the 4-source sentiment engine to vote."""
    NAME = "SentimentAgent"

    def vote(self, df: pd.DataFrame, regime: MarketRegime,
             sentiment_data: dict = None, **kwargs) -> AgentVote:
        if not sentiment_data:
            return AgentVote(self.NAME, "HOLD", 0.40, "no sentiment data")

        decision = sentiment_data.get('decision', 'NEUTRAL')
        total = sentiment_data.get('total_score', 0)

        if decision == "BULLISH":
            conf = min(0.95, 0.60 + abs(total) * 0.05)
            return AgentVote(self.NAME, "BUY", conf, f"BULLISH total={total:+d}")
        elif decision == "BEARISH":
            conf = min(0.95, 0.60 + abs(total) * 0.05)
            return AgentVote(self.NAME, "SELL", conf, f"BEARISH total={total:+d}")
        else:
            return AgentVote(self.NAME, "HOLD", 0.50, f"NEUTRAL total={total:+d}")


class RiskAgent:
    """Checks drawdown, daily P&L, and portfolio risk before voting."""
    NAME = "RiskAgent"

    def vote(self, df: pd.DataFrame, regime: MarketRegime,
             daily_pnl: float = 0, max_daily_loss: float = 15.0,
             open_positions: int = 0, max_positions: int = 3,
             drawdown_pct: float = 0, **kwargs) -> AgentVote:

        # Hard block: daily loss limit
        if daily_pnl <= -max_daily_loss:
            return AgentVote(self.NAME, "HOLD", 0.95, f"daily loss ${daily_pnl:.2f} hit limit")

        # Block if too many positions
        if open_positions >= max_positions:
            return AgentVote(self.NAME, "HOLD", 0.90, f"max positions ({open_positions}/{max_positions})")

        # Reduce conviction if in drawdown
        if drawdown_pct > 15:
            return AgentVote(self.NAME, "HOLD", 0.80, f"drawdown {drawdown_pct:.1f}% too high")
        elif drawdown_pct > 10:
            return AgentVote(self.NAME, "BUY", 0.50, f"moderate drawdown {drawdown_pct:.1f}%")

        # All clear
        return AgentVote(self.NAME, "BUY", 0.75, "risk OK")


class PatternAgent:
    """Detects candlestick patterns: engulfing, doji, hammer."""
    NAME = "PatternAgent"

    def vote(self, df: pd.DataFrame, regime: MarketRegime, **kwargs) -> AgentVote:
        if df is None or df.empty or len(df) < 3:
            return AgentVote(self.NAME, "HOLD", 0.30, "insufficient data")

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        c_open = curr.get('open', 0)
        c_close = curr.get('close', 0)
        c_high = curr.get('high', 0)
        c_low = curr.get('low', 0)
        p_open = prev.get('open', 0)
        p_close = prev.get('close', 0)

        body = abs(c_close - c_open)
        total_range = c_high - c_low if c_high > c_low else 0.0001
        body_ratio = body / total_range

        patterns = []

        # Bullish engulfing: prev red, curr green covers prev body
        if p_close < p_open and c_close > c_open:
            if c_close > p_open and c_open < p_close:
                patterns.append(("bullish_engulfing", +2))

        # Bearish engulfing: prev green, curr red covers prev body
        if p_close > p_open and c_close < c_open:
            if c_close < p_open and c_open > p_close:
                patterns.append(("bearish_engulfing", -2))

        # Hammer (bullish): small body at top, long lower wick
        lower_wick = min(c_open, c_close) - c_low
        upper_wick = c_high - max(c_open, c_close)
        if body_ratio < 0.3 and lower_wick > body * 2 and upper_wick < body:
            patterns.append(("hammer", +1))

        # Doji: very small body (indecision)
        if body_ratio < 0.1:
            patterns.append(("doji", 0))

        if not patterns:
            return AgentVote(self.NAME, "HOLD", 0.40, "no pattern")

        total = sum(p[1] for p in patterns)
        names = [p[0] for p in patterns]

        if total >= 2:
            return AgentVote(self.NAME, "BUY", 0.80, f"patterns: {', '.join(names)}")
        elif total <= -2:
            return AgentVote(self.NAME, "SELL", 0.80, f"patterns: {', '.join(names)}")
        elif total > 0:
            return AgentVote(self.NAME, "BUY", 0.60, f"patterns: {', '.join(names)}")
        elif total < 0:
            return AgentVote(self.NAME, "SELL", 0.60, f"patterns: {', '.join(names)}")
        else:
            return AgentVote(self.NAME, "HOLD", 0.50, f"patterns: {', '.join(names)}")


# ═══════════════════════════════════════════════════════════════════════
# COMMITTEE — Aggregates all agent votes into a single decision
# ═══════════════════════════════════════════════════════════════════════

DEFAULT_WEIGHTS = {
    'TrendAgent': 0.25,
    'MomentumAgent': 0.20,
    'SentimentAgent': 0.25,
    'RiskAgent': 0.20,
    'PatternAgent': 0.10,
}

BUY_THRESHOLD = 0.60
SELL_THRESHOLD = 0.60


class Committee:
    """
    Trading committee — collects votes from 5 agents, produces final decision.
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.agents = [
            TrendAgent(),
            MomentumAgent(),
            SentimentAgent(),
            RiskAgent(),
            PatternAgent(),
        ]
        # Load weights from config or use defaults
        self.weights = {
            'TrendAgent': self.config.get('trend_agent_weight', 0.25),
            'MomentumAgent': self.config.get('momentum_agent_weight', 0.20),
            'SentimentAgent': self.config.get('sentiment_agent_weight', 0.25),
            'RiskAgent': self.config.get('risk_agent_weight', 0.20),
            'PatternAgent': self.config.get('pattern_agent_weight', 0.10),
        }

    def vote(self, df: pd.DataFrame, regime: MarketRegime,
             symbol: str = "", **kwargs) -> Dict:
        """
        Collect all agent votes and compute weighted decision.

        kwargs passed to agents:
            sentiment_data, daily_pnl, max_daily_loss,
            open_positions, max_positions, drawdown_pct

        Returns:
            {
                'decision': 'BUY' | 'SELL' | 'HOLD',
                'buy_score': float,
                'sell_score': float,
                'votes': List[AgentVote],
                'log_line': str,
            }
        """
        votes: List[AgentVote] = []

        for agent in self.agents:
            try:
                v = agent.vote(df, regime, **kwargs)
                votes.append(v)
            except Exception as e:
                logger.error(f"[COMMITTEE] {agent.NAME} error: {e}")
                votes.append(AgentVote(agent.NAME, "HOLD", 0.30, f"error: {e}"))

        # Calculate weighted scores
        buy_score = 0.0
        sell_score = 0.0

        for v in votes:
            w = self.weights.get(v.agent, 0.10)
            if v.decision == "BUY":
                buy_score += w * v.confidence
            elif v.decision == "SELL":
                sell_score += w * v.confidence
            # HOLD contributes to neither

        # Determine final decision
        if buy_score >= BUY_THRESHOLD:
            decision = "BUY"
        elif sell_score >= SELL_THRESHOLD:
            decision = "SELL"
        else:
            decision = "HOLD"

        # Build log lines
        vote_parts = [f"{v.agent}: {v.decision} {v.confidence:.2f}" for v in votes]
        vote_line = " | ".join(vote_parts)

        logger.warning(
            f"[COMMITTEE] {vote_line}"
        )
        logger.warning(
            f"[COMMITTEE] Weighted BUY: {buy_score:.2f} | SELL: {sell_score:.2f} | "
            f"→ FINAL: {decision} {'✅' if decision != 'HOLD' else '⏸'}"
        )

        return {
            'decision': decision,
            'buy_score': round(buy_score, 4),
            'sell_score': round(sell_score, 4),
            'votes': votes,
            'log_line': vote_line,
        }


def check_entry_gates(
    committee_decision: str,
    signal_score: float,
    sentiment_decision: str,
    regime: MarketRegime,
    daily_pnl: float,
    max_daily_loss: float,
    symbol: str,
    open_symbols: set,
    signal_score_threshold: float = 70,
) -> Tuple[bool, str]:
    """
    Gate check — all 6 conditions must pass for a BUY to execute.

    Returns (passed: bool, log_line: str)
    """
    gates = []

    # Gate 1: Committee decision
    g1 = committee_decision == "BUY"
    gates.append(f"committee: {committee_decision} {'✅' if g1 else '❌'}")

    # Gate 2: Signal score
    g2 = signal_score >= signal_score_threshold
    gates.append(f"signal_score: {signal_score:.0f}/{signal_score_threshold:.0f} {'✅' if g2 else '❌'}")

    # Gate 3: Sentiment not BEARISH
    g3 = sentiment_decision != "BEARISH"
    gates.append(f"sentiment: {sentiment_decision} {'✅' if g3 else '❌'}")

    # Gate 4: Regime TRENDING_UP
    g4 = regime == MarketRegime.TRENDING_UP
    gates.append(f"regime: {regime.value} {'✅' if g4 else '❌'}")

    # Gate 5: Daily P&L limit
    g5 = daily_pnl > -max_daily_loss
    gates.append(f"daily_pnl: ${daily_pnl:+.2f} {'✅' if g5 else '❌'}")

    # Gate 6: No duplicate position
    g6 = symbol not in open_symbols
    gates.append(f"duplicate: {'none ✅' if g6 else 'EXISTS ❌'}")

    all_passed = g1 and g2 and g3 and g4 and g5 and g6
    log_line = " | ".join(gates)

    logger.warning(f"[GATE] {log_line}")

    if not all_passed:
        # Find first failed gate for the summary
        failed = []
        if not g1:
            failed.append(f"committee={committee_decision}")
        if not g2:
            failed.append(f"score={signal_score:.0f}")
        if not g3:
            failed.append(f"sentiment={sentiment_decision}")
        if not g4:
            failed.append(f"regime={regime.value}")
        if not g5:
            failed.append(f"pnl=${daily_pnl:.2f}")
        if not g6:
            failed.append("duplicate position")
        logger.warning(f"[GATE] ❌ Blocked by: {', '.join(failed)}")

    return all_passed, log_line
