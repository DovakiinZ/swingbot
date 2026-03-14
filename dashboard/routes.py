"""
Dashboard REST API routes.
Provides JSON endpoints for scanner results, positions, Binance account linking, and overall status.
"""
import os
import json
import logging
import ccxt
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from flask import Flask, jsonify, render_template, request
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

# Resolve project root (where .env lives)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


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
    # Append any new keys not already in file
    for key, val in env.items():
        lines.append(f"{key}={val}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _test_binance_connection(api_key: str, api_secret: str) -> dict:
    """Test Binance API connection. Returns {ok, balance, error}."""
    try:
        exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        })
        balance = exchange.fetch_balance()
        usdt_free = float(balance.get("USDT", {}).get("free", 0))
        usdt_total = float(balance.get("USDT", {}).get("total", 0))
        btc_total = float(balance.get("BTC", {}).get("total", 0))
        return {
            "ok": True,
            "usdt_free": round(usdt_free, 2),
            "usdt_total": round(usdt_total, 2),
            "btc_total": round(btc_total, 6),
        }
    except ccxt.AuthenticationError:
        return {"ok": False, "error": "Authentication failed — invalid API key or secret."}
    except ccxt.ExchangeError as e:
        return {"ok": False, "error": f"Exchange error: {str(e)}"}
    except Exception as e:
        return {"ok": False, "error": f"Connection error: {str(e)}"}


def create_app(store=None, state=None):
    """Create Flask app with dashboard routes."""
    if not FLASK_AVAILABLE:
        logger.warning("Flask not installed — dashboard disabled. pip install flask")
        return None

    app = Flask(__name__,
                template_folder="templates",
                static_folder="templates")

    # =====================
    # Pages
    # =====================
    @app.route("/")
    def index():
        return render_template("index.html")

    # =====================
    # Status & Data APIs
    # =====================
    @app.route("/api/status")
    def api_status():
        if state is None:
            return jsonify({"error": "State not initialized"}), 500
        return jsonify(state.snapshot())

    @app.route("/api/lang")
    def api_lang():
        """Return all i18n strings for current language."""
        from core.i18n import i18n
        return jsonify({"lang": i18n.lang, "strings": i18n.get_all()})

    @app.route("/api/lang/<lang_code>", methods=["POST"])
    def api_set_lang(lang_code):
        """Switch dashboard language."""
        from core.i18n import i18n
        if lang_code in ("en", "ar"):
            i18n.set_lang(lang_code)
            return jsonify({"ok": True, "lang": lang_code})
        return jsonify({"ok": False, "error": "Unsupported language"}), 400

    @app.route("/api/positions")
    def api_positions():
        if state is None:
            return jsonify([])
        return jsonify(state.get("positions_summary", []))

    @app.route("/api/scanner")
    def api_scanner():
        if state is None:
            return jsonify([])
        return jsonify(state.get("scan_results", []))

    @app.route("/api/positions/db")
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

    # =====================
    # Binance Account APIs
    # =====================
    @app.route("/api/binance/status")
    def api_binance_status():
        """Check if Binance API keys are configured and valid."""
        env = _read_env()
        api_key = env.get("BINANCE_API_KEY", "")
        api_secret = env.get("BINANCE_API_SECRET", "")

        has_keys = (
            api_key and api_key != "your_api_key_here"
            and api_secret and api_secret != "your_api_secret_here"
        )

        if not has_keys:
            return jsonify({
                "connected": False,
                "has_keys": False,
                "message": "No API keys configured",
            })

        # Mask key for display: show first 4 + last 4
        masked = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "****"

        result = _test_binance_connection(api_key, api_secret)
        if result["ok"]:
            return jsonify({
                "connected": True,
                "has_keys": True,
                "masked_key": masked,
                "usdt_free": result["usdt_free"],
                "usdt_total": result["usdt_total"],
                "btc_total": result["btc_total"],
            })
        else:
            return jsonify({
                "connected": False,
                "has_keys": True,
                "masked_key": masked,
                "error": result["error"],
            })

    @app.route("/api/binance/connect", methods=["POST"])
    def api_binance_connect():
        """Save Binance API keys after validating the connection."""
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "No data provided"}), 400

        api_key = data.get("api_key", "").strip()
        api_secret = data.get("api_secret", "").strip()

        if not api_key or not api_secret:
            return jsonify({"ok": False, "error": "API Key and Secret are required"}), 400

        # Validate key format (basic sanity)
        if len(api_key) < 10 or len(api_secret) < 10:
            return jsonify({"ok": False, "error": "API keys appear too short"}), 400

        # Test the connection first
        result = _test_binance_connection(api_key, api_secret)
        if not result["ok"]:
            return jsonify({"ok": False, "error": result["error"]}), 400

        # Save to .env
        env = _read_env()
        env["BINANCE_API_KEY"] = api_key
        env["BINANCE_API_SECRET"] = api_secret
        _write_env(env)

        # Also update os.environ for current process
        os.environ["BINANCE_API_KEY"] = api_key
        os.environ["BINANCE_API_SECRET"] = api_secret

        masked = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "****"

        return jsonify({
            "ok": True,
            "masked_key": masked,
            "usdt_free": result["usdt_free"],
            "usdt_total": result["usdt_total"],
            "btc_total": result["btc_total"],
        })

    @app.route("/api/binance/disconnect", methods=["POST"])
    def api_binance_disconnect():
        """Remove Binance API keys from .env."""
        env = _read_env()
        env["BINANCE_API_KEY"] = "your_api_key_here"
        env["BINANCE_API_SECRET"] = "your_api_secret_here"
        _write_env(env)

        os.environ.pop("BINANCE_API_KEY", None)
        os.environ.pop("BINANCE_API_SECRET", None)

        return jsonify({"ok": True, "message": "Binance API keys removed"})

    return app
