"""
Trading hours filter for Swingbot.
Prevents opening new positions during low-liquidity hours
or around scheduled economic events.
Does NOT prevent closing open positions — only new entries.
"""
import logging
from datetime import datetime, timezone
from typing import Tuple

logger = logging.getLogger(__name__)


def is_good_time_to_trade(config: dict) -> Tuple[bool, str]:
    """
    Check if the current time is suitable for opening new positions.

    Blocks new entries during:
    1. Low-liquidity hours (default: 0-4 AM UTC)
    2. Within 1 hour of scheduled economic events

    Returns:
        (True, "OK") if trading is allowed.
        (False, reason) if trading should be skipped.
    """
    hours_config = config.get('trading_hours', {})
    if not hours_config.get('enabled', True):
        return True, "OK"

    now = datetime.now(timezone.utc)
    current_hour = now.hour

    # Check low-liquidity hours
    avoid_hours = hours_config.get('avoid_hours_utc', [0, 1, 2, 3, 4])
    if current_hour in avoid_hours:
        return False, f"Low liquidity hour ({current_hour}:00 UTC)"

    # Check economic events
    events = hours_config.get('economic_events', [])
    for event in events:
        try:
            event_time = datetime.fromisoformat(event.get('time', ''))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            diff_seconds = abs((now - event_time).total_seconds())
            if diff_seconds < 3600:  # within 1 hour
                name = event.get('name', 'Unknown event')
                return False, f"Economic event: {name} (within 1 hour)"
        except (ValueError, TypeError):
            continue

    return True, "OK"
