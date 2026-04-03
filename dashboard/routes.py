"""
Dashboard REST API routes with password protection.
Mobile-first dashboard for swingbot trading bot.
Full API: status, positions, history, settings, notifications, weekly reports.
"""
import os
import json
import time
import logging
import threading
from datetime import datetime, timezone
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
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
LIVE_OK_PATH = PROJECT_ROOT / "LIVE_OK.txt"

# Login page HTML
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Swingbot Login</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #080b10; color: #fff; font-family: 'Tajawal', system-ui, -apple-system, sans-serif;
               display: flex; align-items: center; justify-content: center;
               min-height: 100vh; }
        .card { background: #121820; border-radius: 16px; padding: 40px 32px;
                width: 100%; max-width: 360px; margin: 20px; border: 1px solid #1e2a38; }
        h1 { font-size: 24px; margin-bottom: 8px; color: #00e5a0; }
        p { color: #888; font-size: 14px; margin-bottom: 32px; }
        input { width: 100%; padding: 14px 16px; background: #080b10;
                border: 1px solid #1e2a38; border-radius: 10px; color: #fff;
                font-size: 16px; margin-bottom: 16px; }
        button { width: 100%; padding: 14px; background: #00e5a0;
                 color: #080b10; border: none; border-radius: 10px;
                 font-size: 16px; font-weight: 700; cursor: pointer; }
        button:hover { background: #00cc8e; }
        .error { color: #ff3d5a; font-size: 14px; margin-bottom: 16px; }
    </style>
</head>
<body>
    <div class="card">
        <h1>swingbot</h1>
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


def _load_config() -> dict:
    """Load config.yaml."""
    import yaml
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_config(cfg: dict) -> None:
    """Save config.yaml."""
    import yaml
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)


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

    # ── Shared objects (set later by run.py via app.config) ────────────
    app.config['notifier'] = None
    app.config['conservative_mode'] = None
    app.config['weekly_report'] = None
    app.config['goal_tracker'] = None

    # ── Live prices cache ────────────────────────────────────────────
    _price_cache = {'data': {}, 'updated_at': 0, 'lock': threading.Lock()}

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

    # ── Bot Data APIs ──────────────────────────────────────────────────

    @app.route("/api/status")
    @login_required
    def api_status():
        if state is None:
            return jsonify({"error": "State not initialized"}), 500

        snapshot = state.snapshot() if hasattr(state, 'snapshot') else state

        # Compute next scan seconds
        next_scan = 0
        last_cycle = snapshot.get('last_cycle')
        interval = 600
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

        # Conservative mode status
        cm_status = {"active": False, "reason": "OK", "wins_needed_to_exit": 0}
        cm = app.config.get('conservative_mode')
        if cm:
            try:
                cm_status = cm.get_status()
            except Exception:
                pass

        # Trading hours
        trading_hours_ok = True
        trading_hours_reason = "OK"
        try:
            from core.trading_hours import is_good_time_to_trade
            cfg = config or _load_config()
            trading_hours_ok, trading_hours_reason = is_good_time_to_trade(cfg)
        except Exception:
            pass

        # Compounding phase
        base_balance = (config or {}).get('base_balance', 100.0)
        balance = snapshot.get('total_balance', 0)
        if balance >= base_balance * 5.0:
            phase = 3
        elif balance >= base_balance * 2.5:
            phase = 2
        else:
            phase = 1

        phase_targets = {1: base_balance * 2.5, 2: base_balance * 5.0, 3: base_balance * 10.0}
        phase_starts = {1: base_balance, 2: base_balance * 2.5, 3: base_balance * 5.0}
        target = phase_targets.get(phase, base_balance * 10)
        start = phase_starts.get(phase, base_balance)
        phase_progress = ((balance - start) / (target - start) * 100) if target > start else 0

        # Goal tracker
        goal_data = snapshot.get('goal_tracker', {})
        if not goal_data:
            gt = app.config.get('goal_tracker')
            if gt:
                try:
                    goal_data = gt.get_status(balance)
                except Exception:
                    goal_data = {}

        return jsonify({
            'balance': snapshot.get('total_balance', 0),
            'mode': 'live' if snapshot.get('is_live', False) else 'paper',
            'day_pnl': snapshot.get('daily_pnl', 0),
            'day_pnl_pct': snapshot.get('daily_pnl_pct', 0),
            'circuit_breaker': snapshot.get('breaker_status', 'OK'),
            'conservative_mode': cm_status,
            'sentiment_ok': snapshot.get('sentiment_ok', True),
            'macro_scale': snapshot.get('macro_scale', 1.0),
            'sniper_mode': snapshot.get('sniper_mode', False),
            'ai_confidence': snapshot.get('ai_confidence'),
            'next_scan_seconds': next_scan,
            'trading_hours_ok': trading_hours_ok,
            'trading_hours_reason': trading_hours_reason,
            'scan_results': snapshot.get('scan_results', []),
            'open_positions_count': snapshot.get('open_positions_count', 0),
            'max_open_positions': (config or {}).get('max_open_positions', 3),
            'scan_top_n': (config or {}).get('scan_top_n', 20),
            'scan_interval_minutes': (config or {}).get('scan_interval_minutes', 10),
            'compounding_phase': phase,
            'phase_progress_pct': round(phase_progress, 1),
            'last_updated': snapshot.get('last_cycle', ''),
            'goal_tracker': goal_data,
            'websocket': snapshot.get('websocket', {
                'connected': False,
                'symbols_monitored': 0,
                'momentum_signals_today': 0,
                'last_momentum': '—',
            }),
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
        win_count = 0
        total = len(rows)

        streak = 0
        streak_dir = 0
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

        # Balance history
        balance_history = []
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

        # Best/worst trade
        best_trade = max((t['pnl'] for t in trades), default=0) if trades else 0
        worst_trade = min((t['pnl'] for t in trades), default=0) if trades else 0

        return jsonify({
            'trades': trades[:20],
            'balance_history': balance_history,
            'win_streak': streak,
            'win_rate': round(win_rate, 1),
            'sharpe_ratio': sharpe,
            'total_trades': total,
            'best_trade': round(best_trade, 2),
            'worst_trade': round(worst_trade, 2),
        })

    # ── Weekly Reports ─────────────────────────────────────────────────

    @app.route("/api/weekly-reports")
    @login_required
    def api_weekly_reports():
        wr = app.config.get('weekly_report')
        if not wr:
            return jsonify([])
        return jsonify(wr.get_recent_reports(limit=4))

    @app.route("/api/weekly-reports/<week>")
    @login_required
    def api_weekly_report_detail(week):
        report_dir = PROJECT_ROOT / 'reports' / 'weekly'
        week_safe = week.replace('-', '_')
        json_path = report_dir / f"week_{week_safe}.json"
        if json_path.exists():
            with open(json_path, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f))
        return jsonify({"error": "Report not found"}), 404

    @app.route("/api/send-weekly-report", methods=["POST"])
    @login_required
    def api_send_weekly_report():
        wr = app.config.get('weekly_report')
        notifier = app.config.get('notifier')
        if not wr:
            return jsonify({"success": False, "error": "Weekly report not initialized"}), 500
        result = wr.force_send(notifier)
        if result:
            return jsonify({"success": True, "report": result})
        return jsonify({"success": False, "error": "No trades found this week"}), 404

    # ── Unified Settings APIs (Dashboard Control) ────────────────────

    @app.route("/api/settings/load", methods=["GET"])
    @login_required
    def api_settings_load():
        """Return the complete config.yaml as JSON for dashboard population."""
        cfg = _load_config()
        env = _read_env()

        def mask(val):
            if not val or val in ('', 'your_api_key_here', 'your_api_secret_here'):
                return ''
            if len(val) > 8:
                return val[:4] + '...' + val[-4:]
            return '****'

        # Inject exchange connection status (keys masked)
        cfg['_exchange_keys'] = {
            'mexc_key': mask(env.get('MEXC_API_KEY', '')),
            'mexc_secret_set': bool(env.get('MEXC_API_SECRET', '')),
            'bybit_key': mask(env.get('BYBIT_API_KEY', '')),
            'bybit_secret_set': bool(env.get('BYBIT_API_SECRET', '')),
            'binance_key': mask(env.get('BINANCE_API_KEY', '')),
            'binance_secret_set': bool(env.get('BINANCE_API_SECRET', '')),
        }
        cfg['_trading_mode'] = env.get('TRADING_MODE', 'paper')
        cfg['_live_ok_file'] = LIVE_OK_PATH.exists()
        return jsonify(cfg)

    @app.route("/api/settings/save", methods=["POST"])
    @login_required
    def api_settings_save():
        """
        Save any subset of config keys. Supports dot notation for nested keys.
        Deep-merges with existing config.yaml. Bot picks up changes next cycle.
        API keys are saved to .env, not config.yaml.
        """
        import yaml
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data"}), 400

        try:
            cfg = _load_config()

            # Separate API key updates (go to .env, not config.yaml)
            env_updates = {}
            env_keys = {
                'mexc_api_key': 'MEXC_API_KEY',
                'mexc_api_secret': 'MEXC_API_SECRET',
                'bybit_api_key': 'BYBIT_API_KEY',
                'bybit_api_secret': 'BYBIT_API_SECRET',
                'binance_api_key': 'BINANCE_API_KEY',
                'binance_api_secret': 'BINANCE_API_SECRET',
            }

            config_data = {}
            for key, value in data.items():
                lower_key = key.lower()
                if lower_key in env_keys:
                    env_updates[env_keys[lower_key]] = value
                    os.environ[env_keys[lower_key]] = value
                elif key == 'trading_mode':
                    # trading_mode goes to both config AND .env
                    config_data[key] = value
                    env_updates['TRADING_MODE'] = value
                    os.environ['TRADING_MODE'] = value
                    # Handle live flag + LIVE_OK file
                    if value == 'live':
                        config_data['live'] = True
                        LIVE_OK_PATH.write_text("Enabled from dashboard\n", encoding="utf-8")
                    else:
                        config_data['live'] = False
                        if LIVE_OK_PATH.exists():
                            LIVE_OK_PATH.unlink()
                else:
                    config_data[key] = value

            # Deep merge config changes using dot notation
            def set_nested(d, key_path, val):
                keys = key_path.split(".")
                for k in keys[:-1]:
                    d = d.setdefault(k, {})
                d[keys[-1]] = val

            for key, value in config_data.items():
                if "." in key:
                    set_nested(cfg, key, value)
                else:
                    cfg[key] = value

            # Validate critical constraints
            if cfg.get('risk_per_trade_percent', 3) > 10:
                cfg['risk_per_trade_percent'] = 10
            if cfg.get('min_score', 65) < 0:
                cfg['min_score'] = 0
            if cfg.get('min_score', 65) > 100:
                cfg['min_score'] = 100

            _save_config(cfg)

            # Save .env updates
            if env_updates:
                env = _read_env()
                env.update(env_updates)
                _write_env(env)

            # Reload notifier config if available
            notifier = app.config.get('notifier')
            if notifier:
                notifier.config = cfg
                notifier.notif_config = cfg.get('notifications', {})

            return jsonify({"success": True, "saved": list(data.keys())})

        except Exception as e:
            logger.error(f"[Settings] Save failed: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    # ── Legacy Settings APIs (kept for compatibility) ─────────────────

    @app.route("/api/settings", methods=["GET"])
    @login_required
    def api_settings_get():
        import yaml
        env = _read_env()
        cfg = _load_config()

        def mask(key_val):
            if not key_val or key_val in ('', 'your_api_key_here', 'your_api_secret_here'):
                return ''
            if len(key_val) > 8:
                return key_val[:4] + '...' + key_val[-4:]
            return '****'

        notif = cfg.get('notifications', {})

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
            'mexc_key': mask(env.get('MEXC_API_KEY', '')),
            'mexc_secret_set': bool(env.get('MEXC_API_SECRET', '')),
            'binance_key': mask(env.get('BINANCE_API_KEY', '')),
            'binance_secret_set': bool(
                env.get('BINANCE_API_SECRET', '')
                and env.get('BINANCE_API_SECRET', '') != 'your_api_secret_here'
            ),
            # Risk settings
            'risk_per_trade_percent': cfg.get('risk_per_trade_percent', 3),
            'daily_loss_limit_percent': cfg.get('daily_loss_limit_percent', 2),
            'min_score': cfg.get('min_score', 65),
            # Bot toggles
            'allow_short': cfg.get('allow_short', True),
            'trading_hours_enabled': cfg.get('trading_hours', {}).get('enabled', True),
            'conservative_mode_enabled': cfg.get('conservative_mode', {}).get('enabled', True),
            # Trade control
            'max_open_positions': cfg.get('max_open_positions', 3),
            'scan_top_n': cfg.get('scan_top_n', 20),
            'scan_interval_minutes': cfg.get('scan_interval_minutes', 10),
            # Notifications
            'notifications': notif,
        })

    @app.route("/api/settings/exchange", methods=["POST"])
    @login_required
    def api_settings_exchange():
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "No data"}), 400

        exchange = data.get('exchange', '').lower()
        api_key = data.get('api_key', '').strip()
        api_secret = data.get('api_secret', '').strip()

        if exchange not in ('bybit', 'binance', 'mexc'):
            return jsonify({"ok": False, "error": "Exchange must be bybit, binance, or mexc"}), 400

        env = _read_env()
        prefix = exchange.upper()
        if api_key:
            env[f'{prefix}_API_KEY'] = api_key
            os.environ[f'{prefix}_API_KEY'] = api_key
        if api_secret:
            env[f'{prefix}_API_SECRET'] = api_secret
            os.environ[f'{prefix}_API_SECRET'] = api_secret

        _write_env(env)

        cfg = _load_config()
        cfg['primary_exchange'] = exchange
        _save_config(cfg)

        return jsonify({"ok": True, "exchange": exchange})

    @app.route("/api/settings/bot", methods=["POST"])
    @login_required
    def api_settings_bot():
        """Toggle bot settings: allow_short, trading_hours, conservative_mode, live."""
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "No data"}), 400

        cfg = _load_config()

        if 'allow_short' in data:
            cfg['allow_short'] = bool(data['allow_short'])
        if 'trading_hours_enabled' in data:
            if 'trading_hours' not in cfg:
                cfg['trading_hours'] = {}
            cfg['trading_hours']['enabled'] = bool(data['trading_hours_enabled'])
        if 'conservative_mode_enabled' in data:
            if 'conservative_mode' not in cfg:
                cfg['conservative_mode'] = {}
            cfg['conservative_mode']['enabled'] = bool(data['conservative_mode_enabled'])
        if 'live' in data:
            cfg['live'] = bool(data['live'])
            mode = 'live' if data['live'] else 'paper'
            cfg['trading_mode'] = mode
            env = _read_env()
            env['TRADING_MODE'] = mode
            _write_env(env)
            os.environ['TRADING_MODE'] = mode
            if data['live']:
                LIVE_OK_PATH.write_text("Enabled from dashboard\n", encoding="utf-8")
            elif LIVE_OK_PATH.exists():
                LIVE_OK_PATH.unlink()

        _save_config(cfg)
        return jsonify({"ok": True, "applied": data})

    @app.route("/api/settings/risk", methods=["POST"])
    @login_required
    def api_settings_risk():
        """Update risk management settings."""
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "No data"}), 400

        cfg = _load_config()

        if 'risk_per_trade_percent' in data:
            val = float(data['risk_per_trade_percent'])
            cfg['risk_per_trade_percent'] = max(0.5, min(val, 5.0))
        if 'daily_loss_limit_percent' in data:
            val = float(data['daily_loss_limit_percent'])
            cfg['daily_loss_limit_percent'] = max(0.5, min(val, 10.0))
        if 'min_score' in data:
            val = int(data['min_score'])
            cfg['min_score'] = max(50, min(val, 95))

        _save_config(cfg)
        return jsonify({"ok": True, "applied": {
            'risk_per_trade_percent': cfg.get('risk_per_trade_percent'),
            'daily_loss_limit_percent': cfg.get('daily_loss_limit_percent'),
            'min_score': cfg.get('min_score'),
        }})

    @app.route("/api/settings/notifications", methods=["POST"])
    @login_required
    def api_settings_notifications():
        """Update notification platform settings."""
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "No data"}), 400

        cfg = _load_config()
        if 'notifications' not in cfg:
            cfg['notifications'] = {}

        notif = cfg['notifications']

        # Discord
        if 'discord' in data:
            d = data['discord']
            if 'discord' not in notif:
                notif['discord'] = {'enabled': False, 'channels': {}}
            if 'enabled' in d:
                notif['discord']['enabled'] = bool(d['enabled'])
            if 'channels' in d:
                if 'channels' not in notif['discord']:
                    notif['discord']['channels'] = {}
                for ch, url in d['channels'].items():
                    notif['discord']['channels'][ch] = url

        # Telegram
        if 'telegram' in data:
            t = data['telegram']
            if 'telegram' not in notif:
                notif['telegram'] = {'enabled': False, 'bot_token': '', 'chat_id': ''}
            if 'enabled' in t:
                notif['telegram']['enabled'] = bool(t['enabled'])
            if 'bot_token' in t:
                notif['telegram']['bot_token'] = t['bot_token']
            if 'chat_id' in t:
                notif['telegram']['chat_id'] = t['chat_id']

        # Custom
        if 'custom' in data:
            c = data['custom']
            if 'custom' not in notif:
                notif['custom'] = {'enabled': False, 'webhook_url': '', 'format': 'discord'}
            if 'enabled' in c:
                notif['custom']['enabled'] = bool(c['enabled'])
            if 'webhook_url' in c:
                notif['custom']['webhook_url'] = c['webhook_url']
            if 'format' in c:
                notif['custom']['format'] = c['format']

        _save_config(cfg)

        # Reload notifier config
        notifier = app.config.get('notifier')
        if notifier:
            notifier.config = cfg
            notifier.notif_config = cfg.get('notifications', {})

        return jsonify({"ok": True})

    @app.route("/api/settings/positions", methods=["POST"])
    @login_required
    def api_settings_positions():
        """Update trade control settings: max positions, scan top N, scan interval."""
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "No data"}), 400

        cfg = _load_config()

        if 'max_open_positions' in data:
            val = int(data['max_open_positions'])
            cfg['max_open_positions'] = max(1, min(val, 5))
        if 'scan_top_n' in data:
            val = int(data['scan_top_n'])
            cfg['scan_top_n'] = max(10, min(val, 50))
        if 'scan_interval_minutes' in data:
            val = int(data['scan_interval_minutes'])
            cfg['scan_interval_minutes'] = max(5, min(val, 60))

        _save_config(cfg)

        return jsonify({"ok": True, "applied": {
            'max_open_positions': cfg.get('max_open_positions'),
            'scan_top_n': cfg.get('scan_top_n'),
            'scan_interval_minutes': cfg.get('scan_interval_minutes'),
        }})

    # ── Actions ────────────────────────────────────────────────────────

    @app.route("/api/test-connection", methods=["GET", "POST"])
    @login_required
    def api_test_connection():
        """Test exchange API connection."""
        import ccxt
        data = request.get_json() if request.method == 'POST' else {}
        exchange = (data or {}).get('exchange', (config or {}).get('primary_exchange', 'bybit'))

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
            elif exchange == 'mexc':
                ex = ccxt.mexc({
                    'apiKey': env.get('MEXC_API_KEY', ''),
                    'secret': env.get('MEXC_API_SECRET', ''),
                    'enableRateLimit': True,
                })
                bal = ex.fetch_balance()
                usdt = float(bal.get('USDT', {}).get('free', 0) or 0)
                return jsonify({"ok": True, "balance": round(usdt, 2), "exchange": "MEXC"})
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

    @app.route("/api/test-notification", methods=["POST"])
    @login_required
    def api_test_notification():
        """Test a notification platform."""
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "No data"}), 400

        platform = data.get('platform', '')
        channel = data.get('channel', 'general')

        notifier = app.config.get('notifier')
        if not notifier:
            # Create a temporary notifier with current config
            from core.notifier import Notifier
            cfg = _load_config()
            notifier = Notifier(cfg)

        success, message = notifier.test_platform(platform, channel)
        return jsonify({"success": success, "message": message})

    # ── Language API ───────────────────────────────────────────────────

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

    # ── DB endpoints ──────────────────────────────────────────────────

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

    # ── Stats API ─────────────────────────────────────────────────────

    @app.route("/api/stats")
    @login_required
    def api_stats():
        """Overall bot statistics including Triple-Barrier analysis."""
        if store is None:
            return jsonify({})
        conn = store.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(pnl) as total_pnl
            FROM positions WHERE status = 'CLOSED'
        """)
        row = cursor.fetchone()
        conn.close()

        total = row['total'] or 0
        wins = row['wins'] or 0
        total_pnl = row['total_pnl'] or 0
        win_rate = (wins / total * 100) if total > 0 else 0

        # Triple-Barrier stats
        tb_stats = store.get_triple_barrier_stats()

        return jsonify({
            'total_trades': total,
            'wins': wins,
            'losses': total - wins,
            'win_rate': round(win_rate, 1),
            'total_pnl': round(total_pnl, 2),
            'triple_barrier_stats': tb_stats,
        })

    # ── Live Prices API (MEXC) ────────────────────────────────────────

    @app.route("/api/prices")
    @login_required
    def api_prices():
        """Fetch live tickers from MEXC for scanned symbols. Cached for 5 seconds."""
        now = time.time()
        with _price_cache['lock']:
            if now - _price_cache['updated_at'] < 5 and _price_cache['data']:
                return jsonify(_price_cache['data'])

        # Get symbols from scan results or state
        symbols = []
        if state:
            snapshot = state.snapshot() if hasattr(state, 'snapshot') else state
            scan_results = snapshot.get('scan_results', [])
            symbols = [s['symbol'] for s in scan_results if s.get('symbol')]

        if not symbols and store:
            try:
                db_results = store.get_latest_scan_results()
                symbols = [r.symbol for r in db_results]
            except Exception:
                pass

        if not symbols:
            symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

        try:
            import ccxt
            exchange = ccxt.mexc({'enableRateLimit': True})
            tickers = exchange.fetch_tickers(symbols)

            prices = {}
            for symbol, t in tickers.items():
                if t and t.get('last'):
                    prices[symbol] = {
                        'price':      t['last'],
                        'change_24h': t.get('percentage', 0) or 0,
                        'high_24h':   t.get('high', 0) or 0,
                        'low_24h':    t.get('low', 0) or 0,
                        'volume_24h': t.get('quoteVolume', 0) or 0,
                        'bid':        t.get('bid', 0) or 0,
                        'ask':        t.get('ask', 0) or 0,
                        'updated_at': datetime.now(timezone.utc).isoformat(),
                    }

            with _price_cache['lock']:
                _price_cache['data'] = prices
                _price_cache['updated_at'] = time.time()

            return jsonify(prices)
        except Exception as e:
            # Return cached data if available, otherwise empty
            with _price_cache['lock']:
                if _price_cache['data']:
                    return jsonify(_price_cache['data'])
            logger.error(f"[Prices] MEXC fetch failed: {e}")
            return jsonify({})

    # ── Committee History API ──────────────────────────────────────────

    @app.route("/api/committee/history")
    @login_required
    def api_committee_history():
        """Returns committee decisions with agent accuracy stats."""
        if store is None:
            return jsonify({'decisions': [], 'agent_accuracy': {}, 'veto_stats': {}})

        limit = request.args.get('limit', 50, type=int)
        symbol = request.args.get('symbol', None)

        decisions_raw = store.get_committee_history(limit=limit, symbol=symbol)

        decisions = []
        for d in decisions_raw:
            verdicts = {}
            try:
                verdicts = json.loads(d.get('verdicts_json', '{}') or '{}')
            except (json.JSONDecodeError, TypeError):
                pass

            decisions.append({
                'id':               d.get('id', ''),
                'timestamp':        d.get('timestamp', 0),
                'symbol':           d.get('symbol', ''),
                'approved':         bool(d.get('approved', 0)),
                'final_score':      d.get('final_score', 0),
                'size_multiplier':  d.get('size_multiplier', 1.0),
                'veto_by':          d.get('veto_by'),
                'veto_reason':      d.get('veto_reason'),
                'verdicts':         verdicts,
                'trade_executed':   bool(d.get('trade_executed', 0)),
                'trade_outcome':    d.get('trade_outcome'),
                'trade_pnl':        d.get('trade_pnl'),
            })

        agent_accuracy = store.get_agent_accuracy()
        veto_stats = store.get_veto_stats()

        return jsonify({
            'decisions': decisions,
            'agent_accuracy': agent_accuracy,
            'veto_stats': veto_stats,
        })

    # ── AI Chatbot API ─────────────────────────────────────────────────

    @app.route("/api/chat", methods=["POST"])
    @login_required
    def api_chat():
        """AI chatbot powered by Groq. Reads live bot state and answers questions."""
        import requests as req

        data = request.get_json()
        message = (data or {}).get('message', '').strip()
        history = (data or {}).get('history', [])

        if not message:
            return jsonify({"response": "No message provided", "error": True}), 400

        # Build bot context
        cfg = _load_config()
        bot_state = state.snapshot() if hasattr(state, 'snapshot') else (state or {})

        # Recent log lines
        log_lines = []
        try:
            log_file = PROJECT_ROOT / 'logs' / f"swingbot_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"
            if log_file.exists():
                lines = log_file.read_text(encoding='utf-8', errors='ignore').splitlines()
                log_lines = [l.strip() for l in lines[-30:] if l.strip() and 'WARNING' in l][-10:]
        except Exception:
            pass

        # Format scan results
        scan_lines = []
        for s in bot_state.get('scan_results', [])[:5]:
            sym = s.get('symbol', '?')
            score = s.get('score', 0)
            sig = s.get('signal', 'NONE')
            scan_lines.append(f"- {sym}: score={score:.0f}, signal={sig}")

        # Format positions
        pos_lines = []
        for p in bot_state.get('positions_summary', []):
            sym = p.get('symbol', '?')
            side = p.get('side', '?')
            entry = p.get('entry_price', 0)
            pnl = p.get('unrealized_pnl', 0)
            pos_lines.append(f"- {sym} {side} @ ${entry:.4f} | P&L: ${pnl:.2f}")

        cm = bot_state.get('conservative_mode', {})
        if isinstance(cm, dict):
            cm_active = cm.get('active', False)
        else:
            cm_active = False

        system_prompt = (
            "You are Swingbot's AI assistant — a helpful trading bot advisor.\n"
            "You have access to the bot's live state and recent activity.\n"
            "Answer questions about what the bot is doing, why it made decisions, and how it works.\n"
            "Be conversational, clear, and friendly.\n"
            "If the user writes in Arabic, respond FULLY in Arabic.\n"
            "If the user writes in English, respond in English.\n"
            "Keep responses concise — 2-4 sentences max unless more detail is needed.\n"
            "Never make up data — only use what's provided in the context.\n\n"
            f"CURRENT BOT STATE:\n"
            f"- Mode: {cfg.get('trading_mode', 'paper').upper()}\n"
            f"- Exchange: {cfg.get('primary_exchange', 'mexc').upper()}\n"
            f"- Balance: ${bot_state.get('total_balance', 0):.2f} USDT\n"
            f"- Open Positions: {bot_state.get('open_positions_count', 0)}\n"
            f"- Day P&L: ${bot_state.get('daily_pnl', 0):.2f}\n"
            f"- Last Scan: {bot_state.get('last_cycle', 'unknown')}\n"
            f"- Circuit Breaker: {bot_state.get('breaker_status', 'OK')}\n"
            f"- Conservative Mode: {cm_active}\n"
            f"- Scan Interval: {cfg.get('scan_interval_minutes', 10)} minutes\n"
            f"- Min Score: {cfg.get('min_score', 65)}\n"
            f"- Risk Per Trade: {cfg.get('risk_per_trade_percent', 3)}%\n"
            f"- Max Positions: {cfg.get('max_open_positions', 3)}\n"
            f"- Sentiment Threshold: {cfg.get('sentiment_threshold', 20)}\n"
            f"- Short Selling: {cfg.get('allow_short', False)}\n"
            f"- Scanner Enabled: {cfg.get('scanner', {}).get('enabled', False)}\n\n"
            f"TOP SCAN RESULTS (last cycle):\n"
            f"{chr(10).join(scan_lines) or 'No scan results yet'}\n\n"
            f"OPEN POSITIONS:\n"
            f"{chr(10).join(pos_lines) or 'No open positions'}\n\n"
            f"RECENT LOG (last 10 warnings):\n"
            f"{chr(10).join(log_lines) or 'No logs available'}"
        )

        # Build messages
        messages = []
        for msg in history[-4:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": message})

        # Call Groq API
        api_key = os.getenv('GROQ_API_KEY', '')
        if not api_key:
            return jsonify({
                "response": "GROQ_API_KEY not set. Add it to your .env file to enable the AI chatbot.\nGet a key from console.groq.com",
                "error": True
            })

        try:
            # Groq uses OpenAI-compatible format — system message goes in messages array
            groq_messages = [{"role": "system", "content": system_prompt}] + messages

            response = req.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "max_tokens": 500,
                    "messages": groq_messages,
                },
                timeout=30,
            )

            if response.ok:
                result = response.json()
                reply = result['choices'][0]['message']['content']
                return jsonify({"response": reply, "error": False})
            else:
                return jsonify({
                    "response": f"API error: {response.status_code} — {response.text[:200]}",
                    "error": True
                })

        except Exception as e:
            logger.error(f"[Chat] Groq API error: {e}")
            return jsonify({
                "response": f"Connection error: {str(e)}",
                "error": True
            })

    # ── Server-Sent Events (SSE) — real-time push updates ───────────────
    # (Borrowed from freqtrade's web UI approach — SSE instead of polling)
    # Clients connect once, server pushes updates every 5 seconds.
    # Much more efficient than client polling every 30 seconds.

    @app.route('/api/stream')
    @login_required
    def api_stream():
        """
        SSE endpoint — pushes status updates to the dashboard in real-time.
        The client opens one long-lived connection and receives updates as they happen.

        Usage in JS:
            const es = new EventSource('/api/stream');
            es.onmessage = (e) => { const data = JSON.parse(e.data); ... };
        """
        import time as _time

        def generate():
            while True:
                try:
                    if state is None:
                        data = json.dumps({"error": "State not initialized"})
                    else:
                        snapshot = state.snapshot() if hasattr(state, 'snapshot') else state

                        # Minimal status payload for real-time updates
                        next_scan = 0
                        last_cycle = snapshot.get('last_cycle')
                        interval = (config or {}).get('scan_interval_minutes', 10) * 60
                        if last_cycle:
                            try:
                                last_dt = datetime.fromisoformat(last_cycle)
                                elapsed = (datetime.now(timezone.utc) - last_dt.replace(
                                    tzinfo=timezone.utc if last_dt.tzinfo is None else last_dt.tzinfo
                                )).total_seconds()
                                next_scan = max(0, int(interval - elapsed))
                            except Exception:
                                next_scan = 0

                        data = json.dumps({
                            'balance': snapshot.get('total_balance', 0),
                            'mode': snapshot.get('mode', 'paper'),
                            'day_pnl': snapshot.get('day_pnl', 0),
                            'circuit_breaker': snapshot.get('circuit_breaker', 'OK'),
                            'open_positions_count': snapshot.get('open_positions_count', 0),
                            'next_scan_seconds': next_scan,
                            'ai_confidence': snapshot.get('ai_confidence'),
                            'ml_fallback_active': snapshot.get('ml_fallback_active', False),
                            'ml_rolling_accuracy': snapshot.get('ml_rolling_accuracy'),
                            'last_updated': datetime.now(timezone.utc).isoformat(),
                        })

                    yield f"data: {data}\n\n"
                    _time.sleep(5)  # Push every 5 seconds
                except GeneratorExit:
                    return
                except Exception as e:
                    logger.error(f"[SSE] Stream error: {e}")
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
                    _time.sleep(10)

        from flask import Response
        return Response(
            generate(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',  # Disable nginx buffering
                'Connection': 'keep-alive',
            }
        )

    return app
