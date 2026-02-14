import logging
import json
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime
from typing import Any

def setup_logging(log_dir: str = "logs", console: bool = True, console_level: str = "WARNING", file_level: str = "INFO"):
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, f"swingbot_{datetime.now().strftime('%Y-%m-%d')}.log")
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG) # Catch all, filter by handler

    # Clear existing handlers to avoid duplicates
    root_logger.handlers = []

    # File Handler
    file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    file_handler.setLevel(getattr(logging, file_level.upper(), logging.INFO))
    root_logger.addHandler(file_handler)

    # Console Handler
    if console:
        console_handler = logging.StreamHandler()
        # Concise format for console
        console_handler.setFormatter(logging.Formatter('%(message)s')) 
        console_handler.setLevel(getattr(logging, console_level.upper(), logging.WARNING))
        root_logger.addHandler(console_handler)

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
