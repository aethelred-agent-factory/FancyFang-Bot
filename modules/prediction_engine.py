from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import logging
import os
import sys
import joblib  # noqa: F401
from typing import Dict, Any

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logger = logging.getLogger("prediction_engine")
logger.addHandler(logging.NullHandler())

# --- Legacy Heuristic Weights (used as fallback if model is missing) ---
_DEFAULT_WEIGHTS = {
    "norm_rsi": {"weight": 0.20, "long_direction": -1, "short_direction": 1},
    "norm_ema_slope": {"weight": 0.15, "long_direction": 1, "short_direction": -1},
    "norm_volume_spike": {"weight": 0.10, "long_direction": 1, "short_direction": 1},
    "norm_fr_change": {"weight": 0.10, "long_direction": -1, "short_direction": 1},
    "norm_ob_imbalance": {"weight": 0.15, "long_direction": 1, "short_direction": -1},
    "norm_bb_pct": {"weight": 0.10, "long_direction": -1, "short_direction": 1},
    "norm_adx": {"weight": 0.20, "long_direction": 1, "short_direction": 1},
}

_REGIME_WEIGHT_CONFIG = {
    "TRENDING": {"norm_ema_slope": 0.25, "norm_adx": 0.25, "norm_rsi": 0.10, "norm_bb_pct": 0.05},
    "RANGING": {"norm_rsi": 0.30, "norm_bb_pct": 0.25, "norm_ema_slope": 0.05, "norm_adx": 0.05},
    "VOLATILE": {"norm_volume_spike": 0.25, "norm_ob_imbalance": 0.20, "norm_fr_change": 0.15, "norm_rsi": 0.10, "norm_adx": 0.05},
}


class PredictionEngine:
    def __init__(self):
        self.model = None
        self.feature_names = None
        
        # Load the trained XGBoost model and feature names
        model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models", "xgb_classifier.pkl"))
        features_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models", "feature_names.pkl"))
        
        if os.path.exists(model_path) and os.path.exists(features_path):
            try:
                self.model = joblib.load(model_path)
                self.feature_names = joblib.load(features_path)
                logger.info("XGBoost prediction model loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load XGBoost model: {e}")
        else:
            logger.warning("XGBoost model files not found. Using legacy heuristic weights.")

    def get_prediction_score(
        self, features: Dict[str, Any], direction: str, regime: str = "UNKNOWN"
    ) -> float:
        """
        Calculates a prediction score. If a trained model is available, uses inference.
        Otherwise, falls back to probability-weighted heuristic signals.
        """
        # --- ML Inference Path ---
        if self.model is not None and self.feature_names is not None:
            try:
                # Construct the feature vector in the order expected by the model
                X = [features.get(f, 0.0) for f in self.feature_names]
                
                # predict_proba returns [P(loss), P(win)]
                prob = self.model.predict_proba([X])[0][1]
                
                # Map 0..1 probability to -1..+1 score range
                return (prob - 0.5) * 2.0
            except Exception as e:
                logger.error(f"Inference error: {e}. Falling back to heuristics.")
        
        # --- Legacy Heuristic Path ---
        score = 0.0
        regime_overrides = _REGIME_WEIGHT_CONFIG.get(regime.upper(), {})

        for feature_name, config in _DEFAULT_WEIGHTS.items():
            feature_value = features.get(feature_name)
            if feature_value is None:
                continue

            # Apply regime override if available, else use default weight
            weight = regime_overrides.get(feature_name, config["weight"])
            directional_multiplier = 0

            if direction == "LONG":
                directional_multiplier = config["long_direction"]
            elif direction == "SHORT":
                directional_multiplier = config["short_direction"]

            score += weight * (feature_value * directional_multiplier)

        return score

    def reload(self):
        """Reloads the model from disk."""
        self.__init__()
        logger.info("PredictionEngine model reloaded.")
