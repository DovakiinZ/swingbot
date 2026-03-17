from typing import Dict

TRANSLATIONS = {
    "en": {
        # --- Core Status ---
        "CYCLE_START": "--- Cycle Start",
        "STATUS_LINE": "{timestamp} UTC | {symbol} | ${price:<8.2f} | Sig: {signal:<4} | Pos: {pos_state:<6} | Arm: {arm} | DayPnL: {pnl:>6.2f} | {breaker} | {macro} | Next: {next_wait}s",
        "MACRO_STATUS": "Macro: p={p:.2f} sc={sc:.1f}",

        # --- Signals ---
        "SIGNAL_BUY": "BUY",
        "SIGNAL_SELL": "SELL",
        "SIGNAL_HOLD": "-",

        # --- Position States ---
        "POS_NONE": "FLAT",
        "POS_OPEN": "OPEN",
        "POS_OPENING": "OPENING",
        "POS_CLOSING": "CLOSING",

        # --- Circuit Breakers ---
        "BREAKER_OK": "OK",
        "BREAKER_PAUSED": "PAUSED",
        "BREAKER_LOSS_LIMIT": "LOSS_LIMIT",
        "BREAKER_SENTIMENT": "SENTIMENT",

        # --- Daily Summary ---
        "SUMMARY_HEADER": "=== DAILY SUMMARY ({date}) ===",
        "SUMMARY_STATS": "Trades: {count} | WinRate: {winrate:.1f}% | Expectancy: {expectancy:.2f} | PnL: {pnl:.2f} | MaxDD: {max_dd:.2f}% | BestArm: {best_arm}",

        # --- Startup Banner ---
        "START_MSG": "--- Swingbot Started ({mode}) ---",
        "BANNER_ACCOUNT": "Account: {name}",
        "BANNER_MODE": "Mode: {mode}",
        "BANNER_SYMBOL": "Symbol: {symbol} ({timeframe})",
        "BANNER_BALANCE": "Balance: {free:.2f}/{total:.2f} USDT | {btc:.4f} BTC",
        "BANNER_PAPER_BAL": "Paper Balance: {total:.2f} USDT",
        "BANNER_RISK": "Risk: {risk}%/trade | MaxDD: {max_dd}% | MaxLoss: {max_loss_run} in a row",
        "BANNER_SCANNER_ON": "Scanner: ENABLED | Max Positions: {max_pos} | Portfolio Risk Cap: {risk_cap}%",
        "BANNER_SCANNER_OFF": "Scanner: DISABLED | Single-symbol mode: {symbol}",

        # --- Modes ---
        "MODE_LIVE": "LIVE",
        "MODE_PAPER": "PAPER",

        # --- Warnings ---
        "WARNING_FORCE_PAPER": "WARNING: Live mode requirements not met. Forcing PAPER mode.",

        # --- Logging ---
        "LOGGING_MSG": "Logging: Console={console}, File={file}",

        # --- Help ---
        "HELP_TITLE": "--- Swingbot Help ---",
        "HELP_USAGE": "Usage:",
        "HELP_Paper": "  python run.py             : Run in Paper Mode (Default)",
        "HELP_Live": "  python run.py --live      : Run in LIVE Mode (Requires gates)",
        "HELP_Once": "  python run.py --once      : Run one cycle and exit",
        "HELP_Lang": "  python run.py --lang ar   : Force language (ar/en)",
        "HELP_Guide": "  python run.py --guide     : Show this help menu",
        "HELP_Desc": """
    This bot trades based on RSI+EMA strategy with multi-asset scanning.
    - Paper mode uses virtual balance from config.
    - Live mode requires: config.yaml(live=true), .env(TRADING_MODE=live), LIVE_OK.txt.
    - Scanner mode scans Binance for the best opportunities across all USDT pairs.
        """,

        # --- Scanner ---
        "SCANNER_SCANNING": "Scanning {count} pairs for opportunities...",
        "SCANNER_FOUND": "Found {count} candidates (top {top_n} ranked)",
        "SCANNER_ENTRY": "Entry signal for {symbol} (score={score:.3f}, arm={arm})",
        "SCANNER_SKIP_DUP": "Skipping {symbol} — already have open position",
        "SCANNER_SKIP_RISK": "Skipping {symbol} — risk check: {reason}",
        "SCANNER_BLOCKED_BTC": "Entry blocked for {symbol} — BTC dump signal active",
        "SCANNER_NO_RESULTS": "No scan candidates found",

        # --- Multi-Position ---
        "POSITIONS_SUMMARY": "Open Positions ({count}/{max}): {details}",
        "EXIT_SIGNAL": "Exit signal for {symbol}: {reason}",
        "SLTP_TRIGGER": "SL/TP triggered for {symbol}: {reason}",
        "EXECUTING_BUY": "Buying {symbol}: {size} @ ${price:.2f} (arm={arm})",
        "EXECUTING_SELL": "Selling {symbol}: closing {size}",

        # --- Dashboard ---
        "DASH_TITLE": "SwingBot Dashboard",
        "DASH_SUBTITLE": "Multi-Asset Autonomous Scanner & Trader",
        "DASH_STATUS": "Status Overview",
        "DASH_OPEN_POSITIONS": "Open Positions",
        "DASH_PORTFOLIO": "Portfolio Allocation",
        "DASH_SCANNER": "Scanner Results",
        "DASH_BINANCE": "Binance Account",
        "DASH_BALANCE": "Balance",
        "DASH_DAILY_PNL": "Daily PnL",
        "DASH_SCANNER_STATUS": "Scanner",
        "DASH_BREAKER": "Circuit Breaker",
        "DASH_LAST_CYCLE": "Last Cycle",
        "DASH_SYMBOL": "Symbol",
        "DASH_SIDE": "Side",
        "DASH_ENTRY": "Entry Price",
        "DASH_SIZE": "Size",
        "DASH_SL": "Stop Loss",
        "DASH_TP": "Take Profit",
        "DASH_UNREALIZED": "Unrealized PnL",
        "DASH_SCORE": "Score",
        "DASH_TREND": "Trend",
        "DASH_REGIME": "Regime",
        "DASH_VOL_RANK": "Volume Rank",
        "DASH_NO_POSITIONS": "No open positions",
        "DASH_NO_SCAN": "No scan results yet",
        "DASH_NO_ALLOC": "No positions to display",
        "DASH_CONNECTED": "Connected",
        "DASH_NOT_CONNECTED": "Not Connected",
        "DASH_CONNECT": "Connect Binance",
        "DASH_DISCONNECT": "Disconnect",
        "DASH_API_KEY": "API Key",
        "DASH_API_SECRET": "API Secret",
        "DASH_TEST_CONNECTION": "Test & Save",
        "DASH_CONNECTION_SUCCESS": "Successfully connected to Binance!",
        "DASH_CONNECTION_FAIL": "Connection failed. Check your API keys.",
        "DASH_REFRESH": "Auto-refresh every 30s",
    },

    "ar": {
        # --- حالة النظام ---
        "CYCLE_START": "--- بداية الدورة",
        "STATUS_LINE": "{timestamp} UTC | {symbol} | ${price:<8.2f} | الإشارة: {signal} | الصفقة: {pos_state} | الإعداد: {arm} | ربح اليوم: {pnl:>6.2f} | {breaker} | {macro} | التالي: {next_wait}ث",
        "MACRO_STATUS": "مؤشر الاقتصاد: احتمال={p:.2f} مقياس={sc:.1f}",

        # --- الإشارات ---
        "SIGNAL_BUY": "شراء",
        "SIGNAL_SELL": "بيع",
        "SIGNAL_HOLD": "-",

        # --- حالة الصفقات ---
        "POS_NONE": "لا توجد صفقات",
        "POS_OPEN": "مفتوحة",
        "POS_OPENING": "جاري الفتح",
        "POS_CLOSING": "جاري الإغلاق",

        # --- قواطع الأمان ---
        "BREAKER_OK": "يعمل بشكل طبيعي",
        "BREAKER_PAUSED": "متوقف مؤقتاً",
        "BREAKER_LOSS_LIMIT": "تجاوز حد الخسارة",
        "BREAKER_SENTIMENT": "مشاعر السوق سلبية",

        # --- الملخص اليومي ---
        "SUMMARY_HEADER": "=== الملخص اليومي ({date}) ===",
        "SUMMARY_STATS": "الصفقات: {count} | نسبة النجاح: {winrate:.1f}% | التوقع: {expectancy:.2f} | الربح: {pnl:.2f} | أقصى تراجع: {max_dd:.2f}% | أفضل إعداد: {best_arm}",

        # --- شاشة البداية ---
        "START_MSG": "--- تم تشغيل البوت ({mode}) ---",
        "BANNER_ACCOUNT": "الحساب: {name}",
        "BANNER_MODE": "الوضع: {mode}",
        "BANNER_SYMBOL": "زوج التداول: {symbol} ({timeframe})",
        "BANNER_BALANCE": "الرصيد: {free:.2f}/{total:.2f} USDT | {btc:.4f} BTC",
        "BANNER_PAPER_BAL": "الرصيد التجريبي: {total:.2f} USDT",
        "BANNER_RISK": "المخاطرة: {risk}% لكل صفقة | أقصى خسارة يومية: {max_dd}% | أقصى خسائر متتالية: {max_loss_run}",
        "BANNER_SCANNER_ON": "الماسح: مفعّل | أقصى عدد صفقات: {max_pos} | سقف مخاطرة المحفظة: {risk_cap}%",
        "BANNER_SCANNER_OFF": "الماسح: معطّل | وضع العملة الواحدة: {symbol}",

        # --- الأوضاع ---
        "MODE_LIVE": "حقيقي (LIVE)",
        "MODE_PAPER": "تجريبي (PAPER)",

        # --- التحذيرات ---
        "WARNING_FORCE_PAPER": "تحذير: لم يتم استيفاء شروط الوضع الحقيقي. تم التحويل للوضع التجريبي تلقائياً.",

        # --- السجلات ---
        "LOGGING_MSG": "سجل: واجهة={console}, ملف={file}",

        # --- المساعدة ---
        "HELP_TITLE": "--- مساعدة البوت ---",
        "HELP_USAGE": "طريقة الاستخدام:",
        "HELP_Paper": "  python run.py             : تشغيل بالوضع التجريبي (الافتراضي)",
        "HELP_Live": "  python run.py --live      : تشغيل بالوضع الحقيقي (يتطلب شروط أمان)",
        "HELP_Once": "  python run.py --once      : تشغيل دورة واحدة فقط ثم الخروج",
        "HELP_Lang": "  python run.py --lang ar   : تحديد اللغة (ar=عربي / en=إنجليزي)",
        "HELP_Guide": "  python run.py --guide     : عرض قائمة المساعدة هذه",
        "HELP_Desc": """
    هذا البوت يتداول تلقائياً باستخدام استراتيجية RSI+EMA مع ماسح متعدد العملات.
    - الوضع التجريبي: يستخدم رصيد وهمي من الإعدادات (بدون أموال حقيقية).
    - الوضع الحقيقي: يتطلب ثلاثة شروط: تفعيل live في config.yaml + ملف LIVE_OK.txt + متغير البيئة.
    - وضع الماسح: يفحص كل أزواج USDT في بينانس ويختار أفضل الفرص تلقائياً.
        """,

        # --- الماسح ---
        "SCANNER_SCANNING": "جاري فحص {count} عملة للبحث عن فرص...",
        "SCANNER_FOUND": "تم إيجاد {count} فرصة (أفضل {top_n} مرتبة حسب الجودة)",
        "SCANNER_ENTRY": "إشارة دخول لـ {symbol} (التقييم={score:.3f}, الإعداد={arm})",
        "SCANNER_SKIP_DUP": "تم تخطي {symbol} — يوجد صفقة مفتوحة مسبقاً",
        "SCANNER_SKIP_RISK": "تم تخطي {symbol} — فحص المخاطر: {reason}",
        "SCANNER_BLOCKED_BTC": "تم حظر الدخول لـ {symbol} — إشارة انخفاض بيتكوين نشطة",
        "SCANNER_NO_RESULTS": "لم يتم العثور على فرص مناسبة",

        # --- الصفقات المتعددة ---
        "POSITIONS_SUMMARY": "الصفقات المفتوحة ({count}/{max}): {details}",
        "EXIT_SIGNAL": "إشارة خروج لـ {symbol}: {reason}",
        "SLTP_TRIGGER": "تم تفعيل وقف الخسارة/جني الأرباح لـ {symbol}: {reason}",
        "EXECUTING_BUY": "شراء {symbol}: {size} بسعر ${price:.2f} (الإعداد={arm})",
        "EXECUTING_SELL": "بيع {symbol}: إغلاق {size}",

        # --- لوحة التحكم ---
        "DASH_TITLE": "لوحة تحكم البوت",
        "DASH_SUBTITLE": "ماسح وتاجر آلي متعدد العملات",
        "DASH_STATUS": "نظرة عامة",
        "DASH_OPEN_POSITIONS": "الصفقات المفتوحة",
        "DASH_PORTFOLIO": "توزيع المحفظة",
        "DASH_SCANNER": "نتائج الماسح",
        "DASH_BINANCE": "حساب بينانس",
        "DASH_BALANCE": "الرصيد",
        "DASH_DAILY_PNL": "ربح اليوم",
        "DASH_SCANNER_STATUS": "الماسح",
        "DASH_BREAKER": "قاطع الأمان",
        "DASH_LAST_CYCLE": "آخر دورة",
        "DASH_SYMBOL": "العملة",
        "DASH_SIDE": "الاتجاه",
        "DASH_ENTRY": "سعر الدخول",
        "DASH_SIZE": "الحجم",
        "DASH_SL": "وقف الخسارة",
        "DASH_TP": "جني الأرباح",
        "DASH_UNREALIZED": "الربح غير المحقق",
        "DASH_SCORE": "التقييم",
        "DASH_TREND": "الاتجاه",
        "DASH_REGIME": "حالة السوق",
        "DASH_VOL_RANK": "ترتيب الحجم",
        "DASH_NO_POSITIONS": "لا توجد صفقات مفتوحة",
        "DASH_NO_SCAN": "لا توجد نتائج فحص بعد",
        "DASH_NO_ALLOC": "لا توجد صفقات لعرضها",
        "DASH_CONNECTED": "متصل",
        "DASH_NOT_CONNECTED": "غير متصل",
        "DASH_CONNECT": "ربط حساب بينانس",
        "DASH_DISCONNECT": "قطع الاتصال",
        "DASH_API_KEY": "مفتاح API",
        "DASH_API_SECRET": "المفتاح السري",
        "DASH_TEST_CONNECTION": "اختبار وحفظ",
        "DASH_CONNECTION_SUCCESS": "تم الاتصال بحساب بينانس بنجاح!",
        "DASH_CONNECTION_FAIL": "فشل الاتصال. تحقق من مفاتيح API الخاصة بك.",
        "DASH_REFRESH": "تحديث تلقائي كل 30 ثانية",
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

    def get_all(self) -> dict:
        """Return all translations for current language (used by dashboard API)."""
        return dict(self.data)

# Global instance
i18n = I18n()
