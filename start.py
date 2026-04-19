"""
start.py — Swingbot Auto-Setup & Launcher
==========================================
One command to install everything and start the bot.

Usage:
    python start.py              # Auto-update + setup + start bot
    python start.py --check      # Check only, don't start
    python start.py --reset      # Reset database (with backup) + start
    python start.py --paper      # Force paper mode + start
    python start.py --fast       # Start with 2-minute scan interval
    python start.py --no-update  # Skip git pull (use local code as-is)
"""

import sys
import os
import platform
import subprocess
import shutil
import time
import secrets
from pathlib import Path

# Fix Windows console encoding for Unicode output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Colors ────────────────────────────────────────────────────────────

if platform.system() == "Windows":
    os.system("color")

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):     print(f"  {GREEN}\u2705 {msg}{RESET}")
def err(msg):    print(f"  {RED}\u274c {msg}{RESET}")
def warn(msg):   print(f"  {YELLOW}\u26a0\ufe0f  {msg}{RESET}")
def info(msg):   print(f"  {BLUE}\u2139\ufe0f  {msg}{RESET}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}")

# ── Project root ──────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

# ── Required packages ─────────────────────────────────────────────────

REQUIRED_PACKAGES = [
    "ccxt",
    "pandas",
    "ta",
    "pyyaml",
    "python-dotenv",
    "requests",
    "numpy",
    "flask",
    "schedule",
    "scikit-learn",
]

# ── Default .env template ─────────────────────────────────────────────

ENV_TEMPLATE = """\
# ── MEXC (Primary Exchange) ───────────────────────────────────────────────────
# Get from: mexc.com -> API Management -> Create New Key
# Permissions: Read + Trade ONLY. NEVER enable Withdrawal.
MEXC_API_KEY=
MEXC_API_SECRET=

# ── Bybit (Optional) ──────────────────────────────────────────────────────────
BYBIT_API_KEY=
BYBIT_API_SECRET=

# ── Dashboard ─────────────────────────────────────────────────────────────────
DASHBOARD_PASSWORD=swingbot123
FLASK_SECRET_KEY={flask_key}

# ── AI Chatbot (Groq) ─────────────────────────────────────────────────────────
# Get from: console.groq.com
GROQ_API_KEY=

# ── Notifications ─────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_GENERAL=
DISCORD_WEBHOOK_TRADES=
DISCORD_WEBHOOK_CLOSED=
DISCORD_WEBHOOK_REPORTS=
DISCORD_WEBHOOK_WARNINGS=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL_CONSOLE=WARNING
LOG_LEVEL_FILE=DEBUG

# ── Mode ──────────────────────────────────────────────────────────────────────
TRADING_MODE=paper
"""

# ── Default config.yaml ───────────────────────────────────────────────

CONFIG_TEMPLATE = """\
primary_exchange: mexc
bybit_account_type: spot
trading_mode: paper
scan_interval_minutes: 10
timeframe: 1h
lookback: 200
scan_top_n: 10
scanner:
  enabled: true
  breakout_lookback: 20
  btc_correlation_factor: 0.7
allow_short: true
min_score: 55
min_rr_ratio: 2.0
risk_per_trade_percent: 3.0
max_open_positions: 3
max_portfolio_risk_percent: 5.0
max_single_position_percent: 30.0
daily_loss_limit_percent: 2.0
consecutive_loss_limit: 3
api_failure_limit: 2
base_balance: 1000.0
peak_balance_tracking: true
drawdown_reset_threshold: 0.20
breakout_lookback: 20
breakout_volume_mult: 2.0
breakout_compression_atr: 2.0
db_path: data/swingbot.db
paper_start_balance_usdt: 1000.0
sentiment_threshold: 5
min_volume_usdt: 10000000
polymarket:
  enabled: false
  update_hours: 6
  markets: []
  default_risk_scale_on_failure: 0.7
conservative_mode:
  enabled: true
  consecutive_losses_trigger: 3
  daily_loss_trigger_pct: 50
  drawdown_trigger_pct: 15
  risk_reduction_pct: 50
  wins_to_exit: 2
trading_hours:
  enabled: true
  avoid_hours_utc: [0, 1, 2, 3, 4]
bandit:
  exploration_prob: 0.2
account_name: Swingbot Paper
live: false
show_balances_on_startup: true
lang: en
dashboard:
  enabled: true
  port: 8080
  host: 0.0.0.0
notifications:
  discord:
    enabled: false
    channels:
      general: ''
      trades: ''
      closed: ''
      reports: ''
      warnings: ''
  telegram:
    enabled: false
    bot_token: ''
    chat_id: ''
  custom:
    enabled: false
    webhook_url: ''
    format: discord
"""

BANNER = r"""
  ███████╗██╗    ██╗██╗███╗   ██╗ ██████╗ ██████╗  ██████╗ ████████╗
  ██╔════╝██║    ██║██║████╗  ██║██╔════╝ ██╔══██╗██╔═══██╗╚══██╔══╝
  ███████╗██║ █╗ ██║██║██╔██╗ ██║██║  ███╗██████╔╝██║   ██║   ██║
  ╚════██║██║███╗██║██║██║╚██╗██║██║   ██║██╔══██╗██║   ██║   ██║
  ███████║╚███╔███╔╝██║██║ ╚████║╚██████╔╝██████╔╝╚██████╔╝   ██║
  ╚══════╝ ╚══╝╚══╝ ╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚═════╝  ╚═════╝   ╚═╝
"""


# ═══════════════════════════════════════════════════════════════════════
# AUTO-UPDATE FROM GITHUB
# ═══════════════════════════════════════════════════════════════════════

def auto_update() -> bool:
    """
    Pull latest code from GitHub before starting.

    Returns True if an update was applied (caller should re-exec to load
    the new code). False means either no update was needed, not a git repo,
    git not installed, or the pull failed — in all cases it's safe to continue.

    Safety:
      - Uses --ff-only so we NEVER merge over local commits/uncommitted work
      - Skips silently if .git is missing (user downloaded ZIP)
      - Never crashes the launcher on network errors
    """
    header("0. Checking for updates from GitHub...")

    # Not a git repo → user downloaded ZIP, skip silently
    if not (ROOT / ".git").exists():
        info("Not a git repository — skipping auto-update")
        return False

    # Git binary available?
    try:
        subprocess.run(
            ["git", "--version"],
            capture_output=True, text=True, timeout=10, check=True,
        )
    except Exception:
        warn("git not installed — skipping auto-update")
        return False

    # Fetch latest from remote
    try:
        result = subprocess.run(
            ["git", "fetch", "--quiet"],
            capture_output=True, text=True, timeout=30, cwd=str(ROOT),
        )
        if result.returncode != 0:
            warn(f"git fetch failed (offline?): {result.stderr.strip()[:150]}")
            return False
    except subprocess.TimeoutExpired:
        warn("git fetch timed out — continuing with local code")
        return False
    except Exception as e:
        warn(f"git fetch error: {e} — continuing with local code")
        return False

    # Compare local HEAD with upstream
    try:
        local = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=str(ROOT), check=True,
        ).stdout.strip()
        upstream = subprocess.run(
            ["git", "rev-parse", "@{u}"],
            capture_output=True, text=True, cwd=str(ROOT), check=True,
        ).stdout.strip()
        if local == upstream:
            ok("Already up to date with GitHub")
            return False
    except Exception as e:
        info(f"Could not compare revisions: {e}")
        return False

    # Check for uncommitted changes that would block a fast-forward
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=str(ROOT), check=True,
        ).stdout.strip()
        if dirty:
            warn("Local uncommitted changes detected — skipping auto-update")
            info("Commit or stash your changes to receive updates")
            return False
    except Exception:
        pass

    # Pull using fast-forward only (never merges)
    info("New updates available — pulling from GitHub...")
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only", "--quiet"],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )
        if result.returncode == 0:
            ok("Updated to latest version from GitHub")
            return True
        else:
            warn(f"git pull failed: {result.stderr.strip()[:200]}")
            info("Continuing with current local version")
            return False
    except subprocess.TimeoutExpired:
        warn("git pull timed out — continuing with local code")
        return False
    except Exception as e:
        warn(f"git pull error: {e} — continuing with local code")
        return False


# ═══════════════════════════════════════════════════════════════════════
# CHECKS
# ═══════════════════════════════════════════════════════════════════════

def check_python() -> bool:
    """Check Python version >= 3.10."""
    header("1. Checking Python version...")
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}.{v.micro}"
    if v.major < 3 or (v.major == 3 and v.minor < 10):
        err(f"Python {version_str} — need 3.10 or newer")
        return False
    ok(f"Python {version_str}")
    return True


def create_directories() -> bool:
    """Create required directories."""
    header("2. Creating required directories...")
    dirs = ["data", "logs", "reports/weekly", "reports/daily"]
    for d in dirs:
        p = ROOT / d
        try:
            p.mkdir(parents=True, exist_ok=True)
            ok(f"Directory: {d}/")
        except Exception as e:
            err(f"Directory {d}/: {e}")
    return True


def install_packages() -> bool:
    """Install/upgrade all required packages."""
    header("3. Installing/upgrading packages...")
    all_ok = True

    # First try requirements.txt
    req_file = ROOT / "requirements.txt"
    if req_file.exists():
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_file), "-q"],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                ok("Installed from requirements.txt")
            else:
                warn(f"requirements.txt had issues: {result.stderr[:200]}")
                all_ok = False
        except subprocess.TimeoutExpired:
            warn("Package install timed out — trying individually")
            all_ok = False
        except Exception as e:
            warn(f"Could not run pip: {e}")
            all_ok = False

    # Verify each package individually
    for pkg in REQUIRED_PACKAGES:
        import_name = pkg.replace("-", "_")
        if import_name == "pyyaml":
            import_name = "yaml"
        if import_name == "python_dotenv":
            import_name = "dotenv"
        if import_name == "scikit_learn":
            import_name = "sklearn"
        try:
            __import__(import_name)
            ok(f"Package: {pkg}")
        except ImportError:
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", pkg, "-q"],
                    capture_output=True, text=True, timeout=120
                )
                __import__(import_name)
                ok(f"Package: {pkg} (just installed)")
            except Exception as e:
                err(f"Package: {pkg} — {e}")
                all_ok = False

    return all_ok


def check_env() -> bool:
    """Check .env file exists and has important keys."""
    header("4. Checking .env file...")
    env_path = ROOT / ".env"

    if not env_path.exists():
        # Create from template with generated key
        flask_key = secrets.token_hex(32)
        content = ENV_TEMPLATE.format(flask_key=flask_key)
        env_path.write_text(content, encoding="utf-8")
        ok("Created .env file with defaults")
        warn("DASHBOARD_PASSWORD is default — change it!")
        ok("Generated secure FLASK_SECRET_KEY")
        return True

    ok(".env file exists")

    # Read and validate
    env = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip()

    # Check dashboard password
    if env.get("DASHBOARD_PASSWORD", "") in ("", "swingbot123", "changeme123"):
        warn("DASHBOARD_PASSWORD is default — change it!")

    # Auto-generate FLASK_SECRET_KEY if placeholder or empty
    flask_key = env.get("FLASK_SECRET_KEY", "")
    if not flask_key or flask_key in ("auto-generated-on-first-run", "dev-secret-change-me",
                                       "replace-with-long-random-string", "replace-with-a-long-random-string"):
        new_key = secrets.token_hex(32)
        raw = env_path.read_text(encoding="utf-8")
        if "FLASK_SECRET_KEY=" in raw:
            raw = raw.replace(f"FLASK_SECRET_KEY={flask_key}", f"FLASK_SECRET_KEY={new_key}")
        else:
            raw += f"\nFLASK_SECRET_KEY={new_key}\n"
        env_path.write_text(raw, encoding="utf-8")
        os.environ["FLASK_SECRET_KEY"] = new_key
        ok("Generated secure FLASK_SECRET_KEY")
    else:
        ok("FLASK_SECRET_KEY is set")

    return True


def check_config() -> bool:
    """Check config.yaml exists and has valid YAML syntax."""
    header("5. Checking config.yaml...")
    config_path = ROOT / "config.yaml"

    if not config_path.exists():
        config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        ok("Created config.yaml with safe paper defaults")
        return True

    # Try parsing
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        if not isinstance(cfg, dict):
            raise ValueError("Config is not a valid YAML mapping")
        exchange = cfg.get("primary_exchange", "?")
        mode = cfg.get("trading_mode", "paper")
        ok(f"config.yaml valid | Exchange: {exchange} | Mode: {mode}")
        return True
    except Exception as e:
        warn(f"config.yaml has errors: {e}")
        # Backup and recreate
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup = ROOT / f"config.yaml.backup.{ts}"
        shutil.copy(config_path, backup)
        config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        warn(f"Old config backed up to: {backup.name}")
        ok("Created fresh config.yaml with defaults")
        return True


def check_database() -> bool:
    """Verify database setup."""
    header("6. Checking database...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "storage.check_store"],
            capture_output=True, text=True, timeout=30,
            cwd=str(ROOT)
        )
        if result.returncode == 0:
            ok("Database OK")
            return True
        else:
            # Try to import directly
            try:
                sys.path.insert(0, str(ROOT))
                from storage.sqlite_store import SQLiteStore
                import yaml
                with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                db_path = cfg.get("db_path", "data/swingbot.db")
                store = SQLiteStore(db_path=db_path)
                ok("Database OK (direct check)")
                return True
            except Exception as e2:
                warn(f"Database check: {e2}")
                return False
    except Exception as e:
        warn(f"Database check: {e}")
        return False


def reset_database():
    """Reset database with timestamped backup."""
    header("Resetting database...")
    # Check both possible locations
    for db_rel in ["data/swingbot.db", "swingbot.db"]:
        db_path = ROOT / db_rel
        if db_path.exists():
            ts = time.strftime("%Y%m%d_%H%M%S")
            backup = db_path.parent / f"swingbot_backup_{ts}.db"
            shutil.copy(db_path, backup)
            db_path.unlink()
            ok(f"Database reset — backup saved: {backup.name}")
            return
    info("No database found — fresh start")


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    check_only = "--check" in sys.argv
    do_reset = "--reset" in sys.argv
    skip_update = "--no-update" in sys.argv

    print(f"\n{'=' * 50}")
    print(f"  {BOLD}SWINGBOT — Auto Setup & Launcher{RESET}")
    print(f"{'=' * 50}")

    # Step 0: Auto-update from GitHub (unless --no-update)
    if not skip_update:
        try:
            updated = auto_update()
            if updated:
                # New code pulled — re-exec this script so the updated
                # logic takes effect (we're running the old version).
                info("Restarting launcher with updated code...")
                args = [sys.executable, str(Path(__file__).resolve())] + sys.argv[1:]
                if "--no-update" not in args:
                    args.append("--no-update")  # avoid infinite update loop
                os.execv(sys.executable, args)
        except Exception as e:
            warn(f"Auto-update skipped: {e}")

    # Step 1: Python version (hard stop if too old)
    if not check_python():
        err("Python 3.10+ is required. Please upgrade.")
        sys.exit(1)

    # Step 2: Directories
    try:
        create_directories()
    except Exception as e:
        err(f"Directory creation failed: {e}")

    # Step 3: Packages
    try:
        install_packages()
    except Exception as e:
        err(f"Package installation failed: {e}")

    # Step 4: .env
    try:
        check_env()
    except Exception as e:
        err(f".env check failed: {e}")

    # Step 5: config.yaml
    try:
        check_config()
    except Exception as e:
        err(f"config.yaml check failed: {e}")

    # Reset if requested
    if do_reset:
        try:
            reset_database()
        except Exception as e:
            err(f"Database reset failed: {e}")

    # Step 6: Database
    try:
        check_database()
    except Exception as e:
        warn(f"Database check failed: {e}")

    # Done
    if check_only:
        print(f"\n{'=' * 50}")
        ok("All checks complete")
        print(f"{'=' * 50}\n")
        return

    # Force paper mode if requested
    if "--paper" in sys.argv:
        try:
            import yaml
            config_path = ROOT / "config.yaml"
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            cfg["trading_mode"] = "paper"
            cfg["live"] = False
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
            ok("Forced paper mode")
        except Exception as e:
            warn(f"Could not force paper mode: {e}")

    # Print banner and start
    print(f"\n{'=' * 50}")
    print(f"{GREEN}{BANNER}{RESET}")
    ok("All checks passed — Starting Swingbot...")
    port = 8080
    try:
        import yaml
        with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        port = cfg.get("dashboard", {}).get("port", 8080)
    except Exception:
        pass
    info(f"Dashboard: http://localhost:{port}")
    info("Press Ctrl+C to stop")
    print(f"{'=' * 50}\n")

    # Build args
    bot_args = [sys.executable, "run.py", "--lang", "en"]
    if "--fast" in sys.argv:
        bot_args.append("--fast")

    # Launch bot (replace current process)
    try:
        os.execv(sys.executable, bot_args)
    except Exception:
        # Fallback for Windows edge cases
        try:
            subprocess.run(bot_args, cwd=str(ROOT))
        except KeyboardInterrupt:
            print("\nSwingbot stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
        sys.exit(0)
