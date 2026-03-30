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
    'macro_scale', 'fear_greed', 'hour_of_day', 'day_of_week',
    'btc_correlation'  # Altcoin-BTC correlation (from CryptoSentimentBertRfStrat)
]

MODEL_PATH = Path('data/swingbot_model.pkl')
MIN_TRAINING_SAMPLES = 50
CONFIDENCE_THRESHOLD = 0.70   # Only trade when 70%+ confident


FALLBACK_WINDOW = 20          # Track last N predictions for degradation check
FALLBACK_MIN_ACCURACY = 0.50  # Fall back to scanner-only if accuracy < 50%


class SwingbotModel:
    """Random Forest model for trade signal prediction."""

    def __init__(self):
        self.model = None
        self.is_trained = False
        self._recent_predictions: list = []  # (predicted_win, actual_outcome) pairs
        self._fallback_active = False
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

        Uses walk-forward cross-validation instead of standard k-fold
        to prevent future data leakage in time-series data.
        (Borrowed from stefan-jansen/machine-learning-for-trading)

        Uses 'tb_label' column if available (preferred -- Triple-Barrier).
        Falls back to 'outcome' column for backward compatibility.

        Triple-barrier approach:
          +1 -> positive (strong win -- hit TP on time)
           0 -> negative (skip -- capital tied up)
          -1 -> negative (skip -- loss)
        """
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.calibration import CalibratedClassifierCV
            from sklearn.model_selection import TimeSeriesSplit
            from sklearn.metrics import roc_auc_score

            if len(df) < MIN_TRAINING_SAMPLES:
                return {'error': f'Need {MIN_TRAINING_SAMPLES} samples, have {len(df)}'}

            # Use triple-barrier labels if available
            if 'tb_label' in df.columns and df['tb_label'].notna().sum() >= 30:
                y = (df['tb_label'] == 1).astype(int)
                label_source = "triple_barrier"
                logger.warning("[ML] Training with Triple-Barrier labels")
            else:
                y = df['outcome'].astype(int)
                label_source = "binary_outcome"
                logger.warning("[ML] Training with binary labels (no TB data yet)")

            # Prepare features
            feature_cols = list(FEATURE_COLUMNS)
            X = df[feature_cols].fillna(0)

            # Add time-based features that TB makes relevant
            if 'tb_hours_to_barrier' in df.columns:
                X = X.copy()
                X['hours_to_barrier'] = df['tb_hours_to_barrier'].fillna(48)
                X['hit_tp_fast'] = ((df['tb_barrier_hit'] == 'upper') &
                                    (df['tb_hours_to_barrier'] < 12)).astype(int)
                feature_cols = list(X.columns)

            # Walk-forward CV: train on past, test on future — no leakage
            # (standard k-fold shuffles time order and leaks future data)
            n_splits = min(5, len(df) // 20)  # At least 20 samples per fold
            n_splits = max(2, n_splits)
            tscv = TimeSeriesSplit(n_splits=n_splits)

            wf_scores = []
            for train_idx, test_idx in tscv.split(X):
                X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
                y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

                # Skip fold if only one class in train or test
                if len(y_train.unique()) < 2 or len(y_test.unique()) < 2:
                    continue

                fold_rf = RandomForestClassifier(
                    n_estimators=200,
                    max_features=int(np.sqrt(len(feature_cols))),
                    min_samples_leaf=3,
                    random_state=42,
                    n_jobs=-1,
                    class_weight='balanced'
                )
                fold_rf.fit(X_train, y_train)
                fold_prob = fold_rf.predict_proba(X_test)[:, 1]
                wf_scores.append(roc_auc_score(y_test, fold_prob))

            # Train final model on ALL data with calibration
            n_features_sqrt = int(np.sqrt(len(feature_cols)))
            rf = RandomForestClassifier(
                n_estimators=200,
                max_features=n_features_sqrt,
                min_samples_leaf=3,
                random_state=42,
                n_jobs=-1,
                class_weight='balanced'
            )

            # Use TimeSeriesSplit for calibration CV too
            cal_cv = TimeSeriesSplit(n_splits=max(2, n_splits))
            self.model = CalibratedClassifierCV(rf, cv=cal_cv, method='sigmoid')
            self.model.fit(X, y)

            # Feature importance (from underlying RF after fitting)
            try:
                base_rf = self.model.calibrated_classifiers_[0].estimator
                feat_importance = dict(zip(feature_cols, base_rf.feature_importances_))
                top_features = sorted(feat_importance.items(), key=lambda x: x[1], reverse=True)[:5]
            except Exception:
                top_features = []

            # Save model
            MODEL_PATH.parent.mkdir(exist_ok=True)
            with open(MODEL_PATH, 'wb') as f:
                pickle.dump(self.model, f)

            self.is_trained = True
            self._recent_predictions = []  # Reset tracker on retrain

            wf_auc_mean = float(np.mean(wf_scores)) if wf_scores else 0.0
            wf_auc_std = float(np.std(wf_scores)) if wf_scores else 0.0

            metrics = {
                'samples': len(df),
                'win_rate': float(y.mean()),
                'wf_auc_mean': wf_auc_mean,
                'wf_auc_std': wf_auc_std,
                'wf_folds': len(wf_scores),
                'top_features': top_features,
                'label_source': label_source
            }
            logger.warning(f"[ML] Model trained (walk-forward CV): {metrics}")
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

    def record_outcome(self, predicted_win: bool, actual_outcome: int) -> None:
        """
        Record a prediction outcome for degradation tracking.
        Called when a trade closes — compares what the model predicted
        vs what actually happened.
        (Inspired by freqtrade FreqAI's rolling performance monitor)
        """
        self._recent_predictions.append((predicted_win, actual_outcome == 1))
        if len(self._recent_predictions) > FALLBACK_WINDOW:
            self._recent_predictions = self._recent_predictions[-FALLBACK_WINDOW:]

        # Check if model has degraded
        if len(self._recent_predictions) >= FALLBACK_WINDOW // 2:
            correct = sum(1 for pred, actual in self._recent_predictions if pred == actual)
            accuracy = correct / len(self._recent_predictions)

            if accuracy < FALLBACK_MIN_ACCURACY and not self._fallback_active:
                self._fallback_active = True
                logger.warning(
                    f"[ML_FALLBACK] Model accuracy {accuracy:.0%} < {FALLBACK_MIN_ACCURACY:.0%} "
                    f"over last {len(self._recent_predictions)} trades — falling back to scanner-only. "
                    f"Retrain with: python -m ml.trainer"
                )
            elif accuracy >= FALLBACK_MIN_ACCURACY and self._fallback_active:
                self._fallback_active = False
                logger.warning(f"[ML_RECOVER] Model accuracy recovered to {accuracy:.0%} — re-enabling ML gate")

    def get_rolling_accuracy(self) -> Optional[float]:
        """Returns rolling accuracy over recent predictions, or None if insufficient data."""
        if len(self._recent_predictions) < 5:
            return None
        correct = sum(1 for pred, actual in self._recent_predictions if pred == actual)
        return correct / len(self._recent_predictions)

    def should_enter(self, features: dict, scanner_score: float,
                     min_score: float = 55) -> Tuple[bool, float, str]:
        """
        Full entry gate combining scanner score + model confidence.
        Only enter when BOTH the scanner AND the model agree.
        Auto-falls back to scanner-only if model accuracy degrades.
        Returns (enter, confidence, reason).
        """
        if not self.is_trained or self._fallback_active:
            reason = "scanner_only (model not trained yet)" if not self.is_trained \
                else "scanner_only (model accuracy degraded — retrain needed)"
            enter = scanner_score >= min_score
            return enter, 0.0, reason

        confidence, model_ok = self.predict(features)

        if not model_ok:
            return False, confidence, f"model confidence too low ({confidence:.0%})"

        if scanner_score < min_score:
            return False, confidence, f"scanner score too low ({scanner_score:.0f})"

        return True, confidence, f"model={confidence:.0%} score={scanner_score:.0f}"

    @property
    def confidence_threshold(self) -> float:
        """Return the confidence threshold for trading."""
        return CONFIDENCE_THRESHOLD

    @property
    def fallback_active(self) -> bool:
        """Whether the model has been auto-disabled due to poor accuracy."""
        return self._fallback_active
