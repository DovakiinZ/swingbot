"""
ml/model.py -- Random Forest trading signal model.

Architecture:
- 200 decision trees, each seeing sqrt(n_features) random features
- Final prediction = majority vote across all trees (probability 0.0-1.0)
- Only enter when model confidence >= 0.70 (70%+ of trees agree)
- Calibrated with Platt scaling for reliable probability estimates

This is the Polymarket TECHNIQUE applied to crypto OHLCV data,
NOT Polymarket data itself.
"""

import logging
import pickle
from pathlib import Path
from typing import Optional, Tuple
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Features used by the model (must match trade_features schema)
FEATURE_COLUMNS = [
    'rsi_14', 'rsi_7', 'macd', 'macd_signal', 'macd_hist',
    'ema_fast_slope', 'ema_slow_slope', 'adx',
    'atr_percent', 'bb_position', 'bb_width',
    'volume_ratio', 'scanner_score', 'breakout_detected',
    'macro_scale', 'fear_greed', 'hour_of_day', 'day_of_week'
]

MODEL_PATH = Path('data/swingbot_model.pkl')
MIN_TRAINING_SAMPLES = 50
CONFIDENCE_THRESHOLD = 0.70   # Only trade when 70%+ confident


class SwingbotModel:
    """Random Forest model for trade signal prediction."""

    def __init__(self):
        self.model = None
        self.is_trained = False
        self._load_if_exists()

    def _load_if_exists(self) -> None:
        """Load pre-trained model from disk if available."""
        if MODEL_PATH.exists():
            try:
                with open(MODEL_PATH, 'rb') as f:
                    self.model = pickle.load(f)
                self.is_trained = True
                logger.info(f"[ML] Model loaded from {MODEL_PATH}")
            except Exception as e:
                logger.warning(f"[ML] Could not load model: {e}")

    def train(self, df: pd.DataFrame) -> dict:
        """
        Train Random Forest on historical trade data.
        df must have FEATURE_COLUMNS + 'outcome' column.
        Returns training metrics dict.
        """
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.calibration import CalibratedClassifierCV
            from sklearn.model_selection import cross_val_score

            if len(df) < MIN_TRAINING_SAMPLES:
                return {'error': f'Need {MIN_TRAINING_SAMPLES} samples, have {len(df)}'}

            # Prepare data
            X = df[FEATURE_COLUMNS].fillna(0)
            y = df['outcome'].astype(int)

            # Random Forest -- 200 trees, sqrt features per tree
            n_features_sqrt = int(np.sqrt(len(FEATURE_COLUMNS)))
            rf = RandomForestClassifier(
                n_estimators=200,
                max_features=n_features_sqrt,
                min_samples_leaf=3,
                random_state=42,
                n_jobs=-1,
                class_weight='balanced'
            )

            # Calibrate for reliable probabilities (Platt scaling)
            self.model = CalibratedClassifierCV(rf, cv=5, method='sigmoid')
            self.model.fit(X, y)

            # Cross-validation score
            cv_scores = cross_val_score(self.model, X, y, cv=5, scoring='roc_auc')

            # Feature importance (from underlying RF after fitting)
            try:
                base_rf = self.model.calibrated_classifiers_[0].estimator
                feat_importance = dict(zip(FEATURE_COLUMNS, base_rf.feature_importances_))
                top_features = sorted(feat_importance.items(), key=lambda x: x[1], reverse=True)[:5]
            except Exception:
                top_features = []

            # Save model
            MODEL_PATH.parent.mkdir(exist_ok=True)
            with open(MODEL_PATH, 'wb') as f:
                pickle.dump(self.model, f)

            self.is_trained = True

            metrics = {
                'samples': len(df),
                'win_rate': float(y.mean()),
                'cv_auc_mean': float(cv_scores.mean()),
                'cv_auc_std': float(cv_scores.std()),
                'top_features': top_features
            }
            logger.warning(f"[ML] Model trained: {metrics}")
            return metrics

        except ImportError:
            return {'error': 'scikit-learn not installed. Run: pip install scikit-learn'}
        except Exception as e:
            logger.error(f"[ML] Training failed: {e}")
            return {'error': str(e)}

    def predict(self, features: dict) -> Tuple[float, bool]:
        """
        Predict win probability for a setup.
        Returns (confidence, should_trade).
        """
        if not self.is_trained or self.model is None:
            return 0.0, False

        try:
            row = [features.get(col, 0) or 0 for col in FEATURE_COLUMNS]
            X = pd.DataFrame([row], columns=FEATURE_COLUMNS)
            prob = float(self.model.predict_proba(X)[0][1])   # P(win)
            should_trade = prob >= CONFIDENCE_THRESHOLD
            return prob, should_trade
        except Exception as e:
            logger.error(f"[ML] Prediction failed: {e}")
            return 0.0, False

    def should_enter(self, features: dict, scanner_score: float) -> Tuple[bool, float, str]:
        """
        Full entry gate combining scanner score + model confidence.
        Only enter when BOTH the scanner AND the model agree.
        Returns (enter, confidence, reason).
        """
        if not self.is_trained:
            # Fall back to scanner-only if model not yet trained
            enter = scanner_score >= 65
            return enter, 0.0, "scanner_only (model not trained yet)"

        confidence, model_ok = self.predict(features)

        if not model_ok:
            return False, confidence, f"model confidence too low ({confidence:.0%})"

        if scanner_score < 65:
            return False, confidence, f"scanner score too low ({scanner_score:.0f})"

        return True, confidence, f"model={confidence:.0%} score={scanner_score:.0f}"

    @property
    def confidence_threshold(self) -> float:
        """Return the confidence threshold for trading."""
        return CONFIDENCE_THRESHOLD
