from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import logging
from typing import Dict, List, Optional

import core.phemex_common as pc

logger = logging.getLogger("feature_builder")
logger.addHandler(logging.NullHandler())

class FeatureBuilder:
    def __init__(self):
        # Initialize rolling normalizers for various features
        self._norm_rsi = pc.RollingNormalizer(window=100)
        self._norm_ema_slope = pc.RollingNormalizer(window=100)
        self._norm_volume_spike = pc.RollingNormalizer(window=100)
        self._norm_fr_change = pc.RollingNormalizer(window=100)
        self._norm_ob_imbalance = pc.RollingNormalizer(window=100)
        self._norm_bb_pct = pc.RollingNormalizer(window=100)

    def build_features(self, data: pc.TickerData) -> Dict[str, float]:
        features = {}

        # Normalized RSI: 0-100, centered around 50. Z-score.
        if data.rsi is not None:
            features["norm_rsi"] = self._norm_rsi.update_and_score(data.rsi - 50.0) # centered at 0

        # Normalized EMA Slope
        if data.ema_slope is not None:
            features["norm_ema_slope"] = self._norm_ema_slope.update_and_score(data.ema_slope)

        # Normalized Volume Spike
        if data.vol_spike is not None:
            features["norm_volume_spike"] = self._norm_volume_spike.update_and_score(data.vol_spike - 1.0) # centered at 0

        # Normalized Funding Rate Change
        if data.fr_change is not None:
            features["norm_fr_change"] = self._norm_fr_change.update_and_score(data.fr_change)

        # Normalized Order Book Imbalance: imbalance - 1 (centered at 0)
        if data.ob_imbalance is not None:
            features["norm_ob_imbalance"] = self._norm_ob_imbalance.update_and_score(data.ob_imbalance - 1.0)
        
        # Normalized BB_Pct: bb_pct - 0.5 (centered at 0)
        if data.bb is not None:
            bb_range = data.bb["upper"] - data.bb["lower"]
            if bb_range > 0.0:
                bb_pct = (data.price - data.bb["lower"]) / bb_range
                features["norm_bb_pct"] = self._norm_bb_pct.update_and_score(bb_pct - 0.5)

        return features

    def reset_normalizers(self):
        """Reset all rolling normalizers."""
        self._norm_rsi.reset()
        self._norm_ema_slope.reset()
        self._norm_volume_spike.reset()
        self._norm_fr_change.reset()
        self._norm_ob_imbalance.reset()
        self._norm_bb_pct.reset()
