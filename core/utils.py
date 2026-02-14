import logging
import json
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime
from typing import Any

def setup_logging(log_dir: str = "logs", console: bool = True):
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, f"swingbot_{datetime.now().strftime('%Y%m%d')}.log")
    
    handlers = []
    file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    handlers.append(file_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        handlers.append(console_handler)

    logging.basicConfig(
        level=logging.INFO,
        handlers=handlers
    )

def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0

def load_json(filepath: str) -> Any:
    if not os.path.exists(filepath):
        return {}
    with open(filepath, 'r') as f:
        return json.load(f)

def save_json(filepath: str, data: Any):
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=4)
