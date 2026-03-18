"""
Multi-platform notification system for Swingbot.
Supports Discord (webhooks), Telegram (bot API), and custom webhooks.
Loads settings from config.yaml. If a platform fails, logs error and continues.
"""
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class Notifier:
    """
    Sends notifications to all enabled platforms.
    Loads config from the notifications section of config.yaml.
    If any platform fails, logs the error and continues — never stops the bot.
    """

    def __init__(self, config: dict):
        self.config = config
        self.notif_config = config.get('notifications', {})

    def _get_discord_url(self, channel: str = "general") -> Optional[str]:
        """Get Discord webhook URL for a channel, falling back to general."""
        discord = self.notif_config.get('discord', {})
        if not discord.get('enabled', False):
            return None
        channels = discord.get('channels', {})
        url = channels.get(channel, '') or channels.get('general', '')
        return url if url else None

    def _send_discord(self, payload: dict, channel: str = "general") -> bool:
        """Send a Discord webhook payload."""
        url = self._get_discord_url(channel)
        if not url:
            return False
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code in (200, 204):
                return True
            logger.error(f"[Notifier] Discord error {resp.status_code}: {resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"[Notifier] Discord send failed: {e}")
            return False

    def _send_telegram(self, text: str) -> bool:
        """Send a Telegram message."""
        tg = self.notif_config.get('telegram', {})
        if not tg.get('enabled', False):
            return False
        token = tg.get('bot_token', '')
        chat_id = tg.get('chat_id', '')
        if not token or not chat_id:
            return False
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            resp = requests.post(url, json={
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True,
            }, timeout=10)
            if resp.status_code == 200:
                return True
            logger.error(f"[Notifier] Telegram error {resp.status_code}: {resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"[Notifier] Telegram send failed: {e}")
            return False

    def _send_custom_webhook(self, payload: dict) -> bool:
        """Send to custom webhook URL."""
        custom = self.notif_config.get('custom', {})
        if not custom.get('enabled', False):
            return False
        url = custom.get('webhook_url', '')
        if not url:
            return False
        fmt = custom.get('format', 'discord')
        try:
            if fmt == 'json':
                resp = requests.post(url, json=payload.get('_raw', payload), timeout=10)
            else:
                resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code in (200, 204):
                return True
            logger.error(f"[Notifier] Custom webhook error {resp.status_code}: {resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"[Notifier] Custom webhook send failed: {e}")
            return False

    def _broadcast(self, discord_payload: dict, telegram_text: str,
                   channel: str = "general") -> None:
        """Send to all enabled platforms. Never raises."""
        try:
            self._send_discord(discord_payload, channel)
        except Exception as e:
            logger.error(f"[Notifier] Discord broadcast error: {e}")
        try:
            self._send_telegram(telegram_text)
        except Exception as e:
            logger.error(f"[Notifier] Telegram broadcast error: {e}")
        try:
            self._send_custom_webhook(discord_payload)
        except Exception as e:
            logger.error(f"[Notifier] Custom webhook broadcast error: {e}")

    def _iso_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── Trade Notifications ─────────────────────────────────────────────

    def notify_entry(self, symbol: str, signal, size: float,
                     score: float, arm: int) -> None:
        """Notify about a new trade entry."""
        side_str = signal.side.value if hasattr(signal, 'side') else str(signal)
        direction = "▲ LONG" if side_str == "BUY" else "▼ SHORT"
        price = signal.price if hasattr(signal, 'price') else 0
        tp = signal.take_profit if hasattr(signal, 'take_profit') else 0
        sl = signal.stop_loss if hasattr(signal, 'stop_loss') else 0

        # R:R calculation
        sl_dist = abs(price - sl) if sl and price else 0
        tp_dist = abs(price - tp) if tp and price else 0
        rr = f"{tp_dist / sl_dist:.1f}x" if sl_dist > 0 else "N/A"

        score_emoji = "\U0001f525" if score >= 80 else ""  # fire emoji

        discord_payload = {
            "embeds": [{
                "title": "\u26a1 \u0635\u0641\u0642\u0629 \u062c\u062f\u064a\u062f\u0629",
                "color": 59808,  # 0x00e5a0 green
                "fields": [
                    {"name": "\u0627\u0644\u0639\u0645\u0644\u0629", "value": symbol, "inline": True},
                    {"name": "\u0627\u0644\u0627\u062a\u062c\u0627\u0647", "value": direction, "inline": True},
                    {"name": "\u0627\u0644\u0633\u0639\u0631", "value": f"${price:,.4f}", "inline": True},
                    {"name": "\u0627\u0644\u0647\u062f\u0641 \U0001f3af", "value": f"${tp:,.4f}" if tp else "N/A", "inline": True},
                    {"name": "\u0627\u0644\u0648\u0642\u0641 \U0001f6d1", "value": f"${sl:,.4f}" if sl else "N/A", "inline": True},
                    {"name": "R:R", "value": rr, "inline": True},
                    {"name": "\u0627\u0644\u062f\u0631\u062c\u0629", "value": f"{score:.0f}/100 {score_emoji}", "inline": True},
                ],
                "footer": {"text": "Swingbot"},
                "timestamp": self._iso_now(),
            }]
        }

        tg_text = (
            f"<b>\u26a1 \u0635\u0641\u0642\u0629 \u062c\u062f\u064a\u062f\u0629</b>\n"
            f"{symbol} | {direction}\n"
            f"\u0627\u0644\u0633\u0639\u0631: ${price:,.4f}\n"
            f"\U0001f3af \u0627\u0644\u0647\u062f\u0641: ${tp:,.4f}\n"
            f"\U0001f6d1 \u0627\u0644\u0648\u0642\u0641: ${sl:,.4f}\n"
            f"R:R: {rr} | \u0627\u0644\u062f\u0631\u062c\u0629: {score:.0f}/100"
        )

        self._broadcast(discord_payload, tg_text, channel="trades")

    def notify_exit(self, symbol: str, reason: str, pnl: float,
                    pnl_pct: float, entry_price: float,
                    exit_price: float) -> None:
        """Notify about a trade exit (close)."""
        is_win = pnl > 0
        color = 59808 if is_win else 16727386  # green or red (0xff3d5a)
        title = "\u2705 \u0635\u0641\u0642\u0629 \u0645\u063a\u0644\u0642\u0629 \u2014 \u0631\u0628\u062d" if is_win else "\u274c \u0635\u0641\u0642\u0629 \u0645\u063a\u0644\u0642\u0629 \u2014 \u062e\u0633\u0627\u0631\u0629"
        pnl_str = f"+${pnl:.2f} (+{pnl_pct:.1f}%)" if is_win else f"-${abs(pnl):.2f} ({pnl_pct:.1f}%)"

        discord_payload = {
            "embeds": [{
                "title": title,
                "color": color,
                "fields": [
                    {"name": "\u0627\u0644\u0639\u0645\u0644\u0629", "value": symbol, "inline": True},
                    {"name": "\u0627\u0644\u0633\u0628\u0628", "value": reason, "inline": True},
                    {"name": "\u0627\u0644\u0631\u0628\u062d", "value": pnl_str, "inline": True},
                    {"name": "\u0627\u0644\u062f\u062e\u0648\u0644", "value": f"${entry_price:,.4f}", "inline": True},
                    {"name": "\u0627\u0644\u062e\u0631\u0648\u062c", "value": f"${exit_price:,.4f}", "inline": True},
                ],
                "footer": {"text": "Swingbot"},
                "timestamp": self._iso_now(),
            }]
        }

        tg_text = (
            f"<b>{title}</b>\n"
            f"{symbol} | {reason}\n"
            f"\u0627\u0644\u0631\u0628\u062d: {pnl_str}\n"
            f"\u0627\u0644\u062f\u062e\u0648\u0644: ${entry_price:,.4f} → \u0627\u0644\u062e\u0631\u0648\u062c: ${exit_price:,.4f}"
        )

        self._broadcast(discord_payload, tg_text, channel="closed")

    def notify_circuit_breaker(self, reason: str) -> None:
        """Notify that circuit breaker tripped."""
        discord_payload = {
            "embeds": [{
                "title": "\U0001f6a8 Circuit Breaker",
                "description": reason,
                "color": 16727386,  # red
                "timestamp": self._iso_now(),
            }]
        }
        tg_text = f"<b>\U0001f6a8 Circuit Breaker</b>\n{reason}"
        self._broadcast(discord_payload, tg_text, channel="warnings")

    def notify_conservative_mode(self, reason: str, risk_mult: float) -> None:
        """Notify that conservative mode activated."""
        discord_payload = {
            "embeds": [{
                "title": "\U0001f6e1 \u0648\u0636\u0639 \u0627\u0644\u0645\u062d\u0627\u0641\u0638\u0629",
                "description": f"{reason}\n\u0645\u0636\u0627\u0639\u0641 \u0627\u0644\u0645\u062e\u0627\u0637\u0631\u0629: x{risk_mult}",
                "color": 16106050,  # gold 0xf5c242
                "timestamp": self._iso_now(),
            }]
        }
        tg_text = f"<b>\U0001f6e1 \u0648\u0636\u0639 \u0627\u0644\u0645\u062d\u0627\u0641\u0638\u0629</b>\n{reason}\n\u0645\u0636\u0627\u0639\u0641 \u0627\u0644\u0645\u062e\u0627\u0637\u0631\u0629: x{risk_mult}"
        self._broadcast(discord_payload, tg_text, channel="warnings")

    def notify_daily_report(self, stats: dict) -> None:
        """Send daily trading report."""
        count = stats.get('count', 0)
        wins = stats.get('wins', count * stats.get('winrate', 0) / 100) if count else 0
        losses = count - int(wins)
        winrate = stats.get('winrate', 0)
        pnl = stats.get('pnl', 0)

        discord_payload = {
            "embeds": [{
                "title": "\U0001f4ca \u062a\u0642\u0631\u064a\u0631 \u064a\u0648\u0645\u064a",
                "color": 16106050,  # gold
                "fields": [
                    {"name": "\u0627\u0644\u0635\u0641\u0642\u0627\u062a", "value": str(count), "inline": True},
                    {"name": "\u0627\u0644\u0631\u0627\u0628\u062d\u0629 \u2705", "value": str(int(wins)), "inline": True},
                    {"name": "\u0627\u0644\u062e\u0627\u0633\u0631\u0629 \u274c", "value": str(losses), "inline": True},
                    {"name": "\u0646\u0633\u0628\u0629 \u0627\u0644\u0641\u0648\u0632", "value": f"{winrate:.0f}%", "inline": True},
                    {"name": "\u0631\u0628\u062d \u0627\u0644\u064a\u0648\u0645", "value": f"${pnl:+.2f}", "inline": True},
                ],
                "footer": {"text": "Swingbot"},
                "timestamp": self._iso_now(),
            }]
        }

        tg_text = (
            f"<b>\U0001f4ca \u062a\u0642\u0631\u064a\u0631 \u064a\u0648\u0645\u064a</b>\n"
            f"\u0627\u0644\u0635\u0641\u0642\u0627\u062a: {count} | \u0646\u0633\u0628\u0629 \u0627\u0644\u0641\u0648\u0632: {winrate:.0f}%\n"
            f"\u0631\u0628\u062d \u0627\u0644\u064a\u0648\u0645: ${pnl:+.2f}"
        )

        self._broadcast(discord_payload, tg_text, channel="reports")

    def notify_weekly_report(self, stats: dict) -> None:
        """Send weekly trading report."""
        discord_payload = {
            "embeds": [{
                "title": f"\U0001f4c8 \u0627\u0644\u062a\u0642\u0631\u064a\u0631 \u0627\u0644\u0623\u0633\u0628\u0648\u0639\u064a \u2014 {stats.get('week', '')}",
                "description": stats.get('period', ''),
                "color": 16106050,  # gold
                "fields": [
                    {"name": "\U0001f4ca \u0627\u0644\u0635\u0641\u0642\u0627\u062a", "value": str(stats.get('trades', 0)), "inline": True},
                    {"name": "\u2705 \u0646\u0633\u0628\u0629 \u0627\u0644\u0641\u0648\u0632", "value": f"{stats.get('win_rate', 0):.0f}%", "inline": True},
                    {"name": "\U0001f4b0 \u0627\u0644\u0631\u0628\u062d", "value": f"${stats.get('total_pnl', 0):+.2f}", "inline": True},
                    {"name": "\U0001f4c9 Sharpe", "value": f"{stats.get('sharpe_ratio', 0):.2f}", "inline": True},
                    {"name": "\u26a1 Expectancy", "value": f"${stats.get('expectancy', 0):.2f} / \u0635\u0641\u0642\u0629", "inline": True},
                    {"name": "\u23f1 \u0645\u062a\u0648\u0633\u0637 \u0627\u0644\u0645\u062f\u0629", "value": f"{stats.get('avg_hold_hours', 0):.1f} \u0633\u0627\u0639\u0629", "inline": True},
                    {"name": "\U0001f3c6 \u0623\u0641\u0636\u0644 \u0639\u0645\u0644\u0629", "value": stats.get('top_symbol', 'N/A'), "inline": True},
                    {"name": "\U0001f4b8 \u0623\u0633\u0648\u0623 \u0639\u0645\u0644\u0629", "value": stats.get('worst_symbol', 'N/A'), "inline": True},
                    {"name": "\U0001f4b5 \u0627\u0644\u0631\u0635\u064a\u062f", "value": f"${stats.get('balance_start', 0):.0f} → ${stats.get('balance_end', 0):.0f}", "inline": False},
                    {"name": "\U0001f680 \u0627\u0644\u0646\u0645\u0648", "value": f"+{stats.get('growth_pct', 0):.1f}%", "inline": False},
                ],
                "footer": {"text": "Swingbot"},
                "timestamp": self._iso_now(),
            }]
        }

        tg_text = (
            f"<b>\U0001f4c8 \u0627\u0644\u062a\u0642\u0631\u064a\u0631 \u0627\u0644\u0623\u0633\u0628\u0648\u0639\u064a \u2014 {stats.get('week', '')}</b>\n"
            f"{stats.get('period', '')}\n"
            f"\u0627\u0644\u0635\u0641\u0642\u0627\u062a: {stats.get('trades', 0)} | \u0646\u0633\u0628\u0629 \u0627\u0644\u0641\u0648\u0632: {stats.get('win_rate', 0):.0f}%\n"
            f"\u0627\u0644\u0631\u0628\u062d: ${stats.get('total_pnl', 0):+.2f} | Sharpe: {stats.get('sharpe_ratio', 0):.2f}\n"
            f"\u0627\u0644\u0631\u0635\u064a\u062f: ${stats.get('balance_start', 0):.0f} → ${stats.get('balance_end', 0):.0f}"
        )

        self._broadcast(discord_payload, tg_text, channel="reports")

    def notify_text(self, text: str, channel: str = "general") -> None:
        """Send a plain text notification."""
        discord_payload = {"content": text}
        self._broadcast(discord_payload, text, channel=channel)

    def test_platform(self, platform: str, channel: str = "general") -> tuple:
        """
        Test a notification platform connection.
        Returns (success: bool, message: str).
        """
        test_msg = f"\u2705 Swingbot test notification - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"

        if platform == "discord":
            url = self._get_discord_url(channel)
            if not url:
                return False, "No Discord webhook URL configured for this channel"
            try:
                resp = requests.post(url, json={"content": test_msg}, timeout=10)
                if resp.status_code in (200, 204):
                    return True, "Discord message sent successfully"
                return False, f"Discord error: {resp.status_code} {resp.text[:100]}"
            except Exception as e:
                return False, f"Discord connection failed: {e}"

        elif platform == "telegram":
            tg = self.notif_config.get('telegram', {})
            token = tg.get('bot_token', '')
            chat_id = tg.get('chat_id', '')
            if not token or not chat_id:
                return False, "Telegram bot_token or chat_id not configured"
            try:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                resp = requests.post(url, json={
                    'chat_id': chat_id, 'text': test_msg
                }, timeout=10)
                if resp.status_code == 200:
                    return True, "Telegram message sent successfully"
                return False, f"Telegram error: {resp.status_code} {resp.text[:100]}"
            except Exception as e:
                return False, f"Telegram connection failed: {e}"

        elif platform == "custom":
            custom = self.notif_config.get('custom', {})
            url = custom.get('webhook_url', '')
            if not url:
                return False, "No custom webhook URL configured"
            try:
                resp = requests.post(url, json={"content": test_msg}, timeout=10)
                if resp.status_code in (200, 204):
                    return True, "Custom webhook sent successfully"
                return False, f"Webhook error: {resp.status_code}"
            except Exception as e:
                return False, f"Webhook connection failed: {e}"

        return False, f"Unknown platform: {platform}"
