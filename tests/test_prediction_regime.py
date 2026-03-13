import pytest
from unittest.mock import patch
from modules.prediction_engine import PredictionEngine


def test_prediction_engine_regimes():
    engine = PredictionEngine()
    # Force heuristic path
    engine.model = None

    # Common features for testing
    features = {
        "norm_rsi": 2.0,  # Strong overbought (Z=2)
        "norm_ema_slope": 2.0,  # Strong uptrend (Z=2)
        "norm_volume_spike": 2.0,  # High volume (Z=2)
        "norm_fr_change": 2.0,  # Rising FR (Z=2)
        "norm_ob_imbalance": 2.0,  # High bid-ask imbalance (Z=2)
        "norm_bb_pct": 2.0,  # High in BB (Z=2)
        "norm_adx": 2.0,  # Strong trend (Z=2)
    }

    score_default = float(engine.get_prediction_score(features, "LONG", regime="UNKNOWN"))
    score_trending = float(engine.get_prediction_score(features, "LONG", regime="TRENDING"))
    score_ranging = float(engine.get_prediction_score(features, "LONG", regime="RANGING"))
    score_volatile = float(engine.get_prediction_score(features, "LONG", regime="VOLATILE"))

    # Verify scores are different
    assert score_default != score_trending
    assert score_trending != score_ranging
    assert score_ranging != score_volatile

    # Total should be 1.0 (Manual calculation confirmed)
    assert pytest.approx(score_trending, 0.01) == 1.0


def test_prediction_engine_short_direction():
    engine = PredictionEngine()
    # Force heuristic path
    engine.model = None
    features = {"norm_rsi": 2.0}  # Overbought

    # For SHORT, higher RSI is bearish -> positive score contribution
    # norm_rsi (2.0) * short_direction (1) * weight (0.2) = 0.4
    score_short = float(engine.get_prediction_score(features, "SHORT", regime="UNKNOWN"))
    assert pytest.approx(score_short, 0.01) == 0.4

    # For LONG, higher RSI is bearish -> negative score contribution
    # norm_rsi (2.0) * long_direction (-1) * weight (0.2) = -0.4
    score_long = float(engine.get_prediction_score(features, "LONG", regime="UNKNOWN"))
    assert pytest.approx(score_long, 0.01) == -0.4
