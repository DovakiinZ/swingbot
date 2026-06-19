import subprocess
import time
import os
import hashlib
import sys

CONFIG_FILE = "config.yaml"
BOT_SCRIPT = "run.py"

def get_config_hash():
    if not os.path.exists(CONFIG_FILE):
        return None
    with open(CONFIG_FILE, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()

def git_pull():
    try:
        print("Checking for remote updates...")
        subprocess.run(["git", "pull"], check=True)
    except Exception as e:
        print(f"Git pull failed: {e}")

def start_bot():
    # Pass along any arguments received by the wrapper
    cmd = [sys.executable, BOT_SCRIPT] + sys.argv[1:]
    return subprocess.Popen(cmd)

if __name__ == "__main__":
    print("=== Swingbot Git-Driven Auto-Reload Wrapper ===")
    
    current_hash = get_config_hash()
    process = start_bot()

    try:
        while True:
            # Check every 30 seconds for config changes or git updates
            time.sleep(30)
            
            # 1. Try to pull latest changes from Git
            git_pull()
            
            # 2. Check if config.yaml has changed locally (via pull or manual edit)
            new_hash = get_config_hash()
            
            if new_hash and new_hash != current_hash:
                print("\n[!] Configuration change detected. Restarting bot...")
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                
                current_hash = new_hash
                process = start_bot()
                print("[+] Bot restarted with new configuration.\n")
            
            # 3. Ensure the bot is still running
            if process.poll() is not None:
                print("\n[!] Bot process exited unexpectedly. Restarting in 5s...")
                time.sleep(5)
                process = start_bot()

    except KeyboardInterrupt:
        print("\nStopping wrapper and bot...")
        process.terminate()
        process.wait()
