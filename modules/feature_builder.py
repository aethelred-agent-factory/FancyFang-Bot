from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import logging
from typing import Any, Dict

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
        self._norm_adx = pc.RollingNormalizer(window=100)
        self._norm_fear_greed = pc.RollingNormalizer(window=100)
        self._norm_btc_dom = pc.RollingNormalizer(window=100)
        self._norm_mkt_cap = pc.RollingNormalizer(window=100)

    def build_features(
        self, data: pc.TickerData, market_context: Dict[str, Any]
    ) -> Dict[str, float]:
        features = {}

        # Normalized RSI: 0-100, centered around 50. Z-score.
        if data.rsi is not None:
            features["norm_rsi"] = self._norm_rsi.update_and_score(
                data.rsi - 50.0
            )  # centered at 0

        # Normalized EMA Slope
        if data.ema_slope is not None:
            features["norm_ema_slope"] = self._norm_ema_slope.update_and_score(
                data.ema_slope
            )

        # Normalized Volume Spike
        if data.vol_spike is not None:
            features["norm_volume_spike"] = self._norm_volume_spike.update_and_score(
                data.vol_spike - 1.0
            )  # centered at 0

        # Normalized Funding Rate Change
        if data.fr_change is not None:
            features["norm_fr_change"] = self._norm_fr_change.update_and_score(
                data.fr_change
            )

        # Normalized Order Book Imbalance: imbalance - 1 (centered at 0)
        if data.ob_imbalance is not None:
            features["norm_ob_imbalance"] = self._norm_ob_imbalance.update_and_score(
                data.ob_imbalance - 1.0
            )

        # Normalized BB_Pct: bb_pct - 0.5 (centered at 0)
        if data.bb is not None:
            bb_range = data.bb["upper"] - data.bb["lower"]
            if bb_range > 0.0:
                bb_pct = (data.price - data.bb["lower"]) / bb_range
                features["norm_bb_pct"] = self._norm_bb_pct.update_and_score(
                    bb_pct - 0.5
                )

        # Normalized ADX
        if data.adx is not None:
            features["norm_adx"] = self._norm_adx.update_and_score(data.adx)

        # External data features from market_context
        fear_greed = market_context.get("fear_greed_index")
        if fear_greed is not None:
            features["fear_greed"] = self._norm_fear_greed.update_and_score(fear_greed)

        btc_dom = market_context.get("btc_dominance")
        if btc_dom is not None:
            features["btc_dominance"] = self._norm_btc_dom.update_and_score(btc_dom)

        mkt_cap_chg = market_context.get("total_market_cap_change_24h")
        if mkt_cap_chg is not None:
            features["market_cap_change"] = self._norm_mkt_cap.update_and_score(
                mkt_cap_chg
            )

        features["news_sentiment"] = 0.0  # Placeholder for Phase 2

        return features

    def reset_normalizers(self):
        """Reset all rolling normalizers."""
        self._norm_rsi.reset()
        self._norm_ema_slope.reset()
        self._norm_volume_spike.reset()
        self._norm_fr_change.reset()
        self._norm_ob_imbalance.reset()
        self._norm_bb_pct.reset()
        self._norm_adx.reset()
        self._norm_fear_greed.reset()
        self._norm_btc_dom.reset()
        self._norm_mkt_cap.reset()

