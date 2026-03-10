from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import logging
from typing import Dict, List, Optional

logger = logging.getLogger("prediction_engine")
logger.addHandler(logging.NullHandler())

# Weights for the probability-weighted signal layer
# Positive weights imply that a positive feature value (after directional alignment)
# contributes positively to the overall prediction score.
_PREDICTION_WEIGHTS_CONFIG = {
    "norm_rsi":          {"weight": 0.20, "long_direction": -1, "short_direction": 1}, # -1 means lower RSI (e.g. oversold) is bullish for long
    "norm_ema_slope":    {"weight": 0.15, "long_direction": 1, "short_direction": -1}, # 1 means positive slope is bullish for long
    "norm_volume_spike": {"weight": 0.10, "long_direction": 1, "short_direction": 1},  # 1 means vol spike is bullish for both (capitulation/blow-off)
    "norm_fr_change":    {"weight": 0.10, "long_direction": -1, "short_direction": 1}, # -1 means falling FR (less positive / more negative) is bullish for long
    "norm_ob_imbalance": {"weight": 0.15, "long_direction": 1, "short_direction": -1}, # 1 means positive imbalance (bid>ask) is bullish for long
    "norm_bb_pct":       {"weight": 0.10, "long_direction": -1, "short_direction": 1}, # -1 means below mid BB is bullish for long
    "norm_adx":          {"weight": 0.10, "long_direction": 1, "short_direction": 1},  # trend strength
}

class PredictionEngine:
    def __init__(self):
        pass

    def get_prediction_score(self, features: Dict[str, float], direction: str) -> float:
        """
        Calculates a probability-weighted prediction score based on normalized features (Z-scores).
        A higher positive score indicates a stronger predictive signal in the specified direction.
        The score is a sum of weighted Z-scores, where Z-scores are directionally aligned.
        """
        score = 0.0

        for feature_name, config in _PREDICTION_WEIGHTS_CONFIG.items():
            feature_value = features.get(feature_name)
            if feature_value is None:
                continue

            weight = config["weight"]
            directional_multiplier = 0

            if direction == "LONG":
                directional_multiplier = config["long_direction"]
            elif direction == "SHORT":
                directional_multiplier = config["short_direction"]
            
            score += weight * (feature_value * directional_multiplier)
        
        return score
