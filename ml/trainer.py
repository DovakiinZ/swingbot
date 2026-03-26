"""
ml/trainer.py -- Train the model from collected paper trade data.

Usage:
    python -m ml.trainer               # Train on all data
    python -m ml.trainer --min 100     # Require 100+ samples
    python -m ml.trainer --report      # Show current model stats
"""
import argparse
import sys
import yaml
from storage.sqlite_store import SQLiteStore
from ml.model import SwingbotModel


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Train SwingBot ML model")
    parser.add_argument('--min', type=int, default=50,
                        help='Minimum training samples required')
    parser.add_argument('--report', action='store_true',
                        help='Show current model status')
    args = parser.parse_args()

    with open('config.yaml', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    store = SQLiteStore(db_path=config['db_path'])
    model = SwingbotModel()

    count = store.get_training_data_count()
    print(f"Training samples available: {count}")

    if args.report:
        print(f"Model trained: {model.is_trained}")
        sys.exit(0)

    if count < args.min:
        print(f"Need at least {args.min} samples. Keep paper trading!")
        sys.exit(1)

    df = store.get_training_data()
    if df is None:
        print("Failed to load training data.")
        sys.exit(1)

    metrics = model.train(df)
    print(f"Training complete: {metrics}")
