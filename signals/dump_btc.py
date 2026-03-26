"""
Dump BTC — Multi-Asset Influence Module

When BTC shows bearish signals with high confidence, reduces risk on all entries.
For BTC/USDT: applies the full factor.
For alts: dampens by btc_correlation_factor (default 0.7).
"""
import logging

logger = logging.getLogger(__name__)


def get_btc_risk_factor_for_symbol(symbol: str, intelligence: dict, config: dict) -> float:
    """
    Returns a risk multiplier [0.0 - 1.0] based on BTC dump intelligence.

    Args:
        symbol: The trading pair (e.g., "ETH/USDT", "BTC/USDT")
        intelligence: Dict with current cycle status (macro_prob, risk_scale, etc.)
        config: Full config dict

    Returns:
        float: Risk factor multiplier. 1.0 = no reduction, 0.0 = block entry.
    """
    scanner_conf = config.get('scanner', {})
    btc_correlation_factor = scanner_conf.get('btc_correlation_factor', 0.7)

    # The macro risk_scale from Polymarket/sentiment already captures some BTC risk.
    # This module adds an additional BTC-specific dampening layer.
    risk_scale = intelligence.get('risk_scale', 1.0)
    macro_prob = intelligence.get('macro_prob', 0.5)

    # Determine BTC bearish confidence level
    # risk_scale < 0.6 indicates high bearish confidence
    # risk_scale < 0.8 indicates moderate bearish signal
    if risk_scale >= 0.8:
        # No significant BTC bearish signal — full factor
        btc_factor = 1.0
    elif risk_scale >= 0.6:
        # Moderate bearish — reduce proportionally
        # Map 0.6-0.8 → factor 0.5-1.0
        btc_factor = 0.5 + (risk_scale - 0.6) * 2.5
    else:
        # High bearish confidence — strongly reduce or block
        # Map 0.0-0.6 → factor 0.0-0.5
        btc_factor = max(0.0, risk_scale / 1.2)

    # Apply correlation dampening for alt coins
    if symbol == "BTC/USDT":
        # BTC gets the full dump factor
        final_factor = btc_factor
    else:
        # Alts: dampen the reduction by correlation factor
        # If btc_factor=0.5, correlation=0.7 → alt_factor = 1.0 - (1.0-0.5)*0.7 = 0.65
        reduction = 1.0 - btc_factor
        alt_reduction = reduction * btc_correlation_factor
        final_factor = 1.0 - alt_reduction

    if final_factor < 1.0:
        logger.info(f"Dump BTC factor for {symbol}: {final_factor:.2f} (btc_factor={btc_factor:.2f}, corr={btc_correlation_factor})")

    return max(0.0, min(1.0, final_factor))
