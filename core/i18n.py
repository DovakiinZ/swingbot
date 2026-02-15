from typing import Dict

TRANSLATIONS = {
    "en": {
        "CYCLE_START": "--- Cycle Start",
        "CYCLE_START": "--- Cycle Start",
        "STATUS_LINE": "{timestamp} UTC | {symbol} | ${price:<8.2f} | Sig: {signal:<4} | Pos: {pos_state:<6} | Arm: {arm} | DayPnL: {pnl:>6.2f} | {breaker} | {macro} | Next: {next_wait}s",
        "MACRO_STATUS": "Macro: p={p:.2f} sc={sc:.1f}",
        "SIGNAL_BUY": "BUY",
        "SIGNAL_SELL": "SELL",
        "SIGNAL_HOLD": "-",
        "POS_NONE": "FLAT",
        "POS_OPEN": "OPEN",
        "POS_OPENING": "OPENING",
        "POS_CLOSING": "CLOSING",
        "BREAKER_OK": "OK",
        "BREAKER_PAUSED": "PAUSED",
        "BREAKER_LOSS_LIMIT": "LOSS_LIMIT",
        "BREAKER_SENTIMENT": "SENTIMENT",
        "SUMMARY_HEADER": "=== DAILY SUMMARY ({date}) ===",
        "SUMMARY_STATS": "Trades: {count} | WinRate: {winrate:.1f}% | Expectancy: {expectancy:.2f} | PnL: {pnl:.2f} | MaxDD: {max_dd:.2f}% | BestArm: {best_arm}",
        "START_MSG": "--- Swingbot Started ({mode}) ---",
        "BANNER_ACCOUNT": "Account: {name}",
        "BANNER_MODE": "Mode: {mode}",
        "BANNER_SYMBOL": "Symbol: {symbol} ({timeframe})",
        "BANNER_BALANCE": "Balance: {free:.2f}/{total:.2f} USDT | {btc:.4f} BTC",
        "BANNER_PAPER_BAL": "Paper Balance: {total:.2f} USDT",
        "BANNER_RISK": "Risk: {risk}%/trade | MaxDD: {max_dd}% | MaxLoss: {max_loss_run} in a row",
        "MODE_LIVE": "LIVE",
        "MODE_PAPER": "PAPER",
        "WARNING_FORCE_PAPER": "WARNING: Live mode requirements not met. Forcing PAPER mode.",
        "LOGGING_MSG": "Logging: Console={console}, File={file}",
        "HELP_TITLE": "--- Swingbot Help / مساعد البوت ---",
        "HELP_USAGE": "Usage / الاستخدام:",
        "HELP_Paper": "  python run.py             : Run in Paper Mode (Default) | تشغيل الوضع التجريبي (افتراضي)",
        "HELP_Live": "  python run.py --live      : Run in LIVE Mode (Requires gates) | تشغيل الوضع الحقيقي (يتطلب شروط)",
        "HELP_Once": "  python run.py --once      : Run one cycle and exit | تشغيل دورة واحدة والخروج",
        "HELP_Lang": "  python run.py --lang ar   : Force language (ar/en) | تحديد اللغة (ar/en)",
        "HELP_Guide": "  python run.py --guide     : Show this help menu | عرض هذه القائمة",
        "HELP_Desc": """
    [English]
    This bot trades based on RSI+EMA strategy. 
    - Paper mode checks 'paper_start_balance_usdt' in config.
    - Live mode requires: config.yaml(live=true), .env(TRADING_MODE=live), LIVE_OK.txt.
    
    [عربي]
    هذا البوت يتداول بناءً على استراتيجية RSI+EMA.
    - الوضع التجريبي يعتمد على رصيد وهمي في الإعدادات.
    - الوضع الحقيقي يتطلب: تفعيل live في الملفات، ووجود ملف LIVE_OK.txt.
        """
    },
    "ar": {
        "CYCLE_START": "--- بداية الدورة",
        # For Arabic, we might want to adjust the format to be RTL friendly. 
        # Putting numbers/English text in distinct blocks helps.
        # {timestamp} UTC | {symbol} | {price} $ | إشارة: {signal} | وضع: {pos_state} | ذراع: {arm} | ربح: {pnl} | {breaker} | التالي: {next_wait}ث
        # {timestamp} UTC | {symbol} | {price} $ | إشارة: {signal} | وضع: {pos_state} | ذراع: {arm} | ربح: {pnl} | {breaker} | التالي: {next_wait}ث
        "STATUS_LINE": "{timestamp} UTC | {symbol} | ${price:<8.2f} | إشارة: {signal} | وضع: {pos_state} | إعداد: {arm} | ربح: {pnl:>6.2f} | {breaker} | {macro} | التالي: {next_wait}ث",
        "MACRO_STATUS": "مؤشر ماكرو: p={p:.2f}, scale={sc:.1f}",
        "SIGNAL_BUY": "شراء",
        "SIGNAL_SELL": "بيع",
        "SIGNAL_HOLD": "-",
        "POS_NONE": "لا يوجد",
        "POS_OPEN": "مفتوحة",
        "POS_OPENING": "يفتح",
        "POS_CLOSING": "يغلق",
        "BREAKER_OK": "نظيف",
        "BREAKER_PAUSED": "موقف",
        "BREAKER_LOSS_LIMIT": "حد_خسارة",
        "BREAKER_SENTIMENT": "مشاعر_سوق",
        "SUMMARY_HEADER": "=== ملخص يومي ({date}) ===",
        "SUMMARY_STATS": "صفقات: {count} | نسبة نجاح: {winrate:.1f}% | توقع: {expectancy:.2f} | ربح: {pnl:.2f} | تراجع: {max_dd:.2f}% | أفضل إعداد: {best_arm}",
        "START_MSG": "--- تم تشغيل البوت ({mode}) ---",
        "BANNER_ACCOUNT": "الحساب: {name}",
        "BANNER_MODE": "الوضع: {mode}",
        "BANNER_SYMBOL": "الرمز: {symbol} ({timeframe})",
        "BANNER_BALANCE": "الرصيد: {free:.2f}/{total:.2f} USDT | {btc:.4f} BTC",
        "BANNER_PAPER_BAL": "رصيد تجريبي: {total:.2f} USDT",
        "BANNER_RISK": "مخاطرة: {risk}%/صفقة | أقصى تراجع: {max_dd}% | وقف خسائر متتالية: {max_loss_run}",
        "MODE_LIVE": "حقيقي (LIVE)",
        "MODE_PAPER": "تجريبي (PAPER)",
        "WARNING_FORCE_PAPER": "تحذير: شروط الوضع الحقيقي غير مستوفاة. تم التحويل للوضع التجريبي.",
        "LOGGING_MSG": "سجل: واجهة={console}, ملف={file}",
        "HELP_TITLE": "--- Swingbot Help / مساعد البوت ---",
        "HELP_USAGE": "Usage / الاستخدام:",
        "HELP_Paper": "  python run.py             : Run in Paper Mode (Default) | تشغيل الوضع التجريبي (افتراضي)",
        "HELP_Live": "  python run.py --live      : Run in LIVE Mode (Requires gates) | تشغيل الوضع الحقيقي (يتطلب شروط)",
        "HELP_Once": "  python run.py --once      : Run one cycle and exit | تشغيل دورة واحدة والخروج",
        "HELP_Lang": "  python run.py --lang ar   : Force language (ar/en) | تحديد اللغة (ar/en)",
        "HELP_Guide": "  python run.py --guide     : Show this help menu | عرض هذه القائمة",
        "HELP_Desc": """
    [English]
    This bot trades based on RSI+EMA strategy. 
    - Paper mode checks 'paper_start_balance_usdt' in config.
    - Live mode requires: config.yaml(live=true), .env(TRADING_MODE=live), LIVE_OK.txt.
    
    [عربي]
    هذا البوت يتداول بناءً على استراتيجية RSI+EMA.
    - الوضع التجريبي يعتمد على رصيد وهمي في الإعدادات.
    - الوضع الحقيقي يتطلب: تفعيل live في الملفات، ووجود ملف LIVE_OK.txt.
        """
    }
}

class I18n:
    def __init__(self, lang: str = "en"):
        self.lang = lang
        self.data = TRANSLATIONS.get(lang, TRANSLATIONS["en"])

    def get(self, key: str) -> str:
        return self.data.get(key, key)
        
    def set_lang(self, lang: str):
        self.lang = lang
        self.data = TRANSLATIONS.get(lang, TRANSLATIONS["en"])

# Global instance
i18n = I18n()
