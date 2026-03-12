from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import logging
from typing import Dict

logger = logging.getLogger("prediction_engine")
logger.addHandler(logging.NullHandler())

# Weights for the probability-weighted signal layer
# Positive weights imply that a positive feature value (after directional alignment)
# contributes positively to the overall prediction score.
_DEFAULT_WEIGHTS = {
    "norm_rsi": {
        "weight": 0.20,
        "long_direction": -1,
        "short_direction": 1,
    },  # -1 means lower RSI (e.g. oversold) is bullish for long
    "norm_ema_slope": {
        "weight": 0.15,
        "long_direction": 1,
        "short_direction": -1,
    },  # 1 means positive slope is bullish for long
    "norm_volume_spike": {
        "weight": 0.10,
        "long_direction": 1,
        "short_direction": 1,
    },  # 1 means vol spike is bullish for both (capitulation/blow-off)
    "norm_fr_change": {
        "weight": 0.10,
        "long_direction": -1,
        "short_direction": 1,
    },  # -1 means falling FR (less positive / more negative) is bullish for long
    "norm_ob_imbalance": {
        "weight": 0.15,
        "long_direction": 1,
        "short_direction": -1,
    },  # 1 means positive imbalance (bid>ask) is bullish for long
    "norm_bb_pct": {
        "weight": 0.10,
        "long_direction": -1,
        "short_direction": 1,
    },  # -1 means below mid BB is bullish for long
    "norm_adx": {
        "weight": 0.20,
        "long_direction": 1,
        "short_direction": 1,
    },  # trend strength
}

# Regime-specific weight overrides
_REGIME_WEIGHT_CONFIG = {
    "TRENDING": {
        "norm_ema_slope": 0.25,
        "norm_adx": 0.25,
        "norm_rsi": 0.10,
        "norm_bb_pct": 0.05,
    },
    "RANGING": {
        "norm_rsi": 0.30,
        "norm_bb_pct": 0.25,
        "norm_ema_slope": 0.05,
        "norm_adx": 0.05,
    },
    "VOLATILE": {
        "norm_volume_spike": 0.25,
        "norm_ob_imbalance": 0.20,
        "norm_fr_change": 0.15,
        "norm_rsi": 0.10,
        "norm_adx": 0.05,
    },
}


class PredictionEngine:
    def __init__(self):
        pass

    def get_prediction_score(
        self, features: Dict[str, float], direction: str, regime: str = "UNKNOWN"
    ) -> float:
        """
        Calculates a probability-weighted prediction score based on normalized features (Z-scores).
        A higher positive score indicates a stronger predictive signal in the specified direction.
        The score is a sum of weighted Z-scores, where Z-scores are directionally aligned.

        The weight for each feature is dynamically adjusted based on the current market regime.
        """
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
