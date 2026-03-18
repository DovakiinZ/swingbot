"""
Dashboard REST API routes with password protection.
Mobile-first dashboard for swingbot trading bot.
"""
import os
import json
import time
import logging
from functools import wraps
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from flask import Flask, jsonify, render_template, request, session, redirect, render_template_string
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

# Resolve project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

# Login page HTML
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Swingbot Login</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #0d0d0d; color: #fff; font-family: system-ui, -apple-system, sans-serif;
               display: flex; align-items: center; justify-content: center;
               min-height: 100vh; }
        .card { background: #1a1a2e; border-radius: 16px; padding: 40px 32px;
                width: 100%; max-width: 360px; margin: 20px; }
        h1 { font-size: 24px; margin-bottom: 8px; }
        p { color: #888; font-size: 14px; margin-bottom: 32px; }
        input { width: 100%; padding: 14px 16px; background: #0d0d0d;
                border: 1px solid #333; border-radius: 10px; color: #fff;
                font-size: 16px; margin-bottom: 16px; }
        button { width: 100%; padding: 14px; background: #00ff88;
                 color: #000; border: none; border-radius: 10px;
                 font-size: 16px; font-weight: 700; cursor: pointer; }
        button:hover { background: #00cc6e; }
        .error { color: #ff4757; font-size: 14px; margin-bottom: 16px; }
    </style>
</head>
<body>
    <div class="card">
        <h1>Swingbot</h1>
        <p>Enter your dashboard password</p>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="POST">
            <input type="password" name="password"
                   placeholder="Password" autofocus>
            <button type="submit">Enter Dashboard</button>
        </form>
    </div>
</body>
</html>
"""


def _read_env() -> dict:
    """Read .env file into a dict."""
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                env[key.strip()] = val.strip()
    return env


def _write_env(env: dict):
    """Write dict back to .env file, preserving comments."""
    lines = []
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                lines.append(line)
                continue
            if "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in env:
                    lines.append(f"{key}={env.pop(key)}")
                else:
                    lines.append(line)
            else:
                lines.append(line)
    for key, val in env.items():
        lines.append(f"{key}={val}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_app(store=None, state=None, config: dict = None):
    """Create Flask app with dashboard routes and password protection."""
    if not FLASK_AVAILABLE:
        logger.warning("Flask not installed -- dashboard disabled. pip install flask")
        return None

    app = Flask(__name__,
                template_folder="templates",
                static_folder="templates")

    app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-secret-change-me')
    DASHBOARD_PASSWORD = os.getenv('DASHBOARD_PASSWORD', 'swingbot123')

    # --- Auth decorator --------------------------------------------------------

    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get('logged_in'):
                return redirect('/login')
            return f(*args, **kwargs)
        return decorated

    # --- Auth routes -----------------------------------------------------------

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = None
        if request.method == 'POST':
            if request.form.get('password') == DASHBOARD_PASSWORD:
                session['logged_in'] = True
                return redirect('/')
            error = 'Wrong password'
        return render_template_string(LOGIN_HTML, error=error)

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect('/login')

    # --- Pages -----------------------------------------------------------------

    @app.route("/")
    @login_required
    def index():
        return render_template("index.html")

    # --- Status & Data APIs ----------------------------------------------------

    @app.route("/api/status")
    @login_required
    def api_status():
        if state is None:
            return jsonify({"error": "State not initialized"}), 500

        snapshot = state.snapshot() if hasattr(state, 'snapshot') else state

        # Compute next scan seconds from last_cycle
        next_scan = 0
        last_cycle = snapshot.get('last_cycle')
        interval = 600  # default 10 min
        if config:
            interval = config.get('scan_interval_minutes', 10) * 60
        if last_cycle:
            try:
                from datetime import datetime
                last_dt = datetime.fromisoformat(last_cycle)
                elapsed = (datetime.utcnow() - last_dt).total_seconds()
                next_scan = max(0, int(interval - elapsed))
            except Exception:
                next_scan = 0

        return jsonify({
            'balance': snapshot.get('total_balance', 0),
            'mode': 'live' if snapshot.get('is_live', False) else 'paper',
            'day_pnl': snapshot.get('daily_pnl', 0),
            'day_pnl_pct': snapshot.get('daily_pnl_pct', 0),
            'circuit_breaker': snapshot.get('breaker_status', 'OK'),
            'sentiment_ok': snapshot.get('sentiment_ok', True),
            'macro_scale': snapshot.get('macro_scale', 1.0),
            'ai_confidence': snapshot.get('ai_confidence'),
            'next_scan_seconds': next_scan,
            'scan_results': snapshot.get('scan_results', []),
            'open_positions_count': snapshot.get('open_positions_count', 0),
            'last_updated': snapshot.get('last_cycle', ''),
        })

    @app.route("/api/positions")
    @login_required
    def api_positions():
        if state is None:
            return jsonify([])
        snapshot = state.snapshot() if hasattr(state, 'snapshot') else state
        positions = snapshot.get('positions_summary', [])

        result = []
        for p in positions:
            entry = p.get('entry_price', 0)
            current = p.get('current_price', entry)
            amount = p.get('amount', 0)
            side = p.get('side', 'BUY')

            if side == 'BUY':
                unrealized = (current - entry) * amount
            else:
                unrealized = (entry - current) * amount

            unrealized_pct = (unrealized / (entry * amount) * 100) if entry and amount else 0

            result.append({
                'symbol': p.get('symbol', ''),
                'side': 'LONG' if side == 'BUY' else 'SHORT',
                'entry_price': entry,
                'current_price': current,
                'unrealized_pnl': p.get('unrealized_pnl', unrealized),
                'unrealized_pnl_pct': unrealized_pct,
                'stop_loss': p.get('stop_loss', 0),
                'take_profit': p.get('take_profit', 0),
                'score': p.get('score', 0),
                'opened_ago_seconds': int(time.time() - (p.get('entry_time', 0) / 1000)) if p.get('entry_time') else 0,
            })
        return jsonify(result)

    @app.route("/api/history")
    @login_required
    def api_history():
        if store is None:
            return jsonify({
                'trades': [], 'balance_history': [],
                'win_streak': 0, 'win_rate': 0, 'sharpe_ratio': None, 'total_trades': 0
            })

        # Get recent closed positions
        conn = store.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, side, pnl, pnl_percent, exit_reason, exit_time
            FROM positions WHERE status = 'CLOSED'
            ORDER BY exit_time DESC LIMIT 30
        """)
        rows = cursor.fetchall()
        conn.close()

        trades = []
        balance_history = []
        win_count = 0
        total = len(rows)

        # Compute win streak from most recent trades
        streak = 0
        streak_dir = 0  # 1 = wins, -1 = losses
        for row in rows:
            pnl = row['pnl'] or 0
            trades.append({
                'symbol': row['symbol'],
                'side': 'LONG' if row['side'] == 'BUY' else 'SHORT',
                'pnl': round(pnl, 2),
                'pnl_pct': round(row['pnl_percent'] or 0, 2),
                'reason': row['exit_reason'] or '',
                'closed_at': row['exit_time'],
            })
            if pnl > 0:
                win_count += 1

        # Calculate streak from ordered list
        for t in trades:
            if t['pnl'] > 0:
                if streak_dir >= 0:
                    streak += 1
                    streak_dir = 1
                else:
                    break
            elif t['pnl'] < 0:
                if streak_dir <= 0:
                    streak -= 1
                    streak_dir = -1
                else:
                    break
            else:
                break

        # Balance history (cumulative from trades, reversed to chronological)
        running = 0
        for t in reversed(trades):
            running += t['pnl']
            balance_history.append(round(running, 2))

        win_rate = (win_count / total * 100) if total > 0 else 0

        # Sharpe ratio
        sharpe = None
        if total >= 3:
            import math
            log_returns = []
            for t in trades:
                pnl_pct = t['pnl_pct']
                if pnl_pct != 0:
                    try:
                        log_returns.append(math.log(1 + pnl_pct / 100))
                    except (ValueError, ZeroDivisionError):
                        pass
            if len(log_returns) >= 3:
                import numpy as np
                mean_r = np.mean(log_returns)
                std_r = np.std(log_returns)
                if std_r > 0:
                    sharpe = round(float(mean_r / std_r), 2)

        return jsonify({
            'trades': trades[:20],
            'balance_history': balance_history,
            'win_streak': streak,
            'win_rate': round(win_rate, 1),
            'sharpe_ratio': sharpe,
            'total_trades': total,
        })

    # --- Language API ----------------------------------------------------------

    @app.route("/api/lang")
    @login_required
    def api_lang():
        from core.i18n import i18n
        return jsonify({"lang": i18n.lang, "strings": i18n.get_all()})

    @app.route("/api/lang/<lang_code>", methods=["POST"])
    @login_required
    def api_set_lang(lang_code):
        from core.i18n import i18n
        if lang_code in ("en", "ar"):
            i18n.set_lang(lang_code)
            return jsonify({"ok": True, "lang": lang_code})
        return jsonify({"ok": False, "error": "Unsupported language"}), 400

    # --- DB endpoints ----------------------------------------------------------

    @app.route("/api/positions/db")
    @login_required
    def api_positions_db():
        if store is None:
            return jsonify([])
        positions = store.get_open_positions()
        return jsonify([
            {
                "id": p.id,
                "symbol": p.symbol,
                "side": p.side.value,
                "entry_price": p.entry_price,
                "amount": p.amount,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "entry_time": p.entry_time,
            }
            for p in positions
        ])

    @app.route("/api/scanner/db")
    @login_required
    def api_scanner_db():
        if store is None:
            return jsonify([])
        results = store.get_latest_scan_results()
        return jsonify([
            {
                "symbol": r.symbol,
                "score": r.score,
                "rsi": r.rsi,
                "atr_pct": r.atr_pct,
                "volume_rank": r.volume_rank,
                "trend": r.trend,
                "regime": r.regime,
                "scanned_at": r.scanned_at,
            }
            for r in results
        ])

    # --- Settings API (Exchange + Live Mode) -------------------------------------

    CONFIG_PATH = PROJECT_ROOT / "config.yaml"
    LIVE_OK_PATH = PROJECT_ROOT / "LIVE_OK.txt"

    @app.route("/api/settings", methods=["GET"])
    @login_required
    def api_settings_get():
        """Return current settings for the settings panel."""
        import yaml
        env = _read_env()

        # Read config.yaml
        cfg = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

        # Mask API keys for display
        def mask(key_val):
            if not key_val or key_val in ('', 'your_api_key_here', 'your_api_secret_here'):
                return ''
            if len(key_val) > 8:
                return key_val[:4] + '...' + key_val[-4:]
            return '****'

        return jsonify({
            'exchange': cfg.get('primary_exchange', 'bybit'),
            'mode': env.get('TRADING_MODE', 'paper'),
            'live_config': cfg.get('live', False),
            'live_ok_file': LIVE_OK_PATH.exists(),
            'live_env': env.get('TRADING_MODE', 'paper').lower() == 'live',
            'all_gates_pass': (
                env.get('TRADING_MODE', 'paper').lower() == 'live'
                and LIVE_OK_PATH.exists()
                and cfg.get('live', False) is True
            ),
            'bybit_key': mask(env.get('BYBIT_API_KEY', '')),
            'bybit_secret_set': bool(env.get('BYBIT_API_SECRET', '')),
            'binance_key': mask(env.get('BINANCE_API_KEY', '')),
            'binance_secret_set': bool(
                env.get('BINANCE_API_SECRET', '')
                and env.get('BINANCE_API_SECRET', '') != 'your_api_secret_here'
            ),
        })

    @app.route("/api/settings/exchange", methods=["POST"])
    @login_required
    def api_settings_exchange():
        """Save exchange API keys and set primary exchange."""
        import yaml
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "No data"}), 400

        exchange = data.get('exchange', '').lower()
        api_key = data.get('api_key', '').strip()
        api_secret = data.get('api_secret', '').strip()

        if exchange not in ('bybit', 'binance'):
            return jsonify({"ok": False, "error": "Exchange must be bybit or binance"}), 400

        # Save API keys to .env
        env = _read_env()
        if exchange == 'bybit':
            if api_key:
                env['BYBIT_API_KEY'] = api_key
                os.environ['BYBIT_API_KEY'] = api_key
            if api_secret:
                env['BYBIT_API_SECRET'] = api_secret
                os.environ['BYBIT_API_SECRET'] = api_secret
        elif exchange == 'binance':
            if api_key:
                env['BINANCE_API_KEY'] = api_key
                os.environ['BINANCE_API_KEY'] = api_key
            if api_secret:
                env['BINANCE_API_SECRET'] = api_secret
                os.environ['BINANCE_API_SECRET'] = api_secret

        _write_env(env)

        # Update config.yaml primary_exchange
        cfg = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        cfg['primary_exchange'] = exchange
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

        return jsonify({"ok": True, "exchange": exchange})

    @app.route("/api/settings/mode", methods=["POST"])
    @login_required
    def api_settings_mode():
        """Toggle between paper and live mode. Sets all 3 gates."""
        import yaml
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "No data"}), 400

        mode = data.get('mode', 'paper').lower()
        if mode not in ('paper', 'live'):
            return jsonify({"ok": False, "error": "Mode must be paper or live"}), 400

        # Gate 1: .env TRADING_MODE
        env = _read_env()
        env['TRADING_MODE'] = mode
        _write_env(env)
        os.environ['TRADING_MODE'] = mode

        # Gate 2: LIVE_OK.txt
        if mode == 'live':
            LIVE_OK_PATH.write_text("Enabled from dashboard\n", encoding="utf-8")
        else:
            if LIVE_OK_PATH.exists():
                LIVE_OK_PATH.unlink()

        # Gate 3: config.yaml live flag
        cfg = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        cfg['live'] = (mode == 'live')
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

        # Check if API keys are set for the selected exchange
        exchange = cfg.get('primary_exchange', 'bybit')
        warning = None
        if mode == 'live':
            if exchange == 'bybit':
                if not env.get('BYBIT_API_KEY') or not env.get('BYBIT_API_SECRET'):
                    warning = "Bybit API keys not set! Add them in Exchange Setup first."
            elif exchange == 'binance':
                bk = env.get('BINANCE_API_KEY', '')
                if not bk or bk == 'your_api_key_here':
                    warning = "Binance API keys not set! Add them in Exchange Setup first."

        return jsonify({
            "ok": True,
            "mode": mode,
            "warning": warning,
            "note": "Restart the bot for changes to take effect."
        })

    @app.route("/api/settings/test-connection", methods=["POST"])
    @login_required
    def api_settings_test():
        """Test exchange API connection."""
        import ccxt
        data = request.get_json() or {}
        exchange = data.get('exchange', 'bybit')

        env = _read_env()

        try:
            if exchange == 'bybit':
                ex = ccxt.bybit({
                    'apiKey': env.get('BYBIT_API_KEY', ''),
                    'secret': env.get('BYBIT_API_SECRET', ''),
                    'enableRateLimit': True,
                })
                bal = ex.fetch_balance()
                usdt = float(bal.get('USDT', {}).get('free', 0) or 0)
                return jsonify({"ok": True, "balance": round(usdt, 2), "exchange": "Bybit"})
            elif exchange == 'binance':
                ex = ccxt.binance({
                    'apiKey': env.get('BINANCE_API_KEY', ''),
                    'secret': env.get('BINANCE_API_SECRET', ''),
                    'enableRateLimit': True,
                })
                bal = ex.fetch_balance()
                usdt = float(bal.get('USDT', {}).get('free', 0) or 0)
                return jsonify({"ok": True, "balance": round(usdt, 2), "exchange": "Binance"})
            else:
                return jsonify({"ok": False, "error": "Unknown exchange"}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    return app
