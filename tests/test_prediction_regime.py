import pytest
from modules.prediction_engine import PredictionEngine


def test_prediction_engine_regimes():
    engine = PredictionEngine()
    engine.model = None  # Force heuristic path for regime testing

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

    # Direction: LONG
    # For LONG:
    # norm_rsi (2.0) * -1 = -2.0
    # norm_ema_slope (2.0) * 1 = 2.0
    # norm_volume_spike (2.0) * 1 = 2.0
    # norm_fr_change (2.0) * -1 = -2.0
    # norm_ob_imbalance (2.0) * 1 = 2.0
    # norm_bb_pct (2.0) * -1 = -2.0
    # norm_adx (2.0) * 1 = 2.0

    # Weights:
    # Default: rsi:0.2, ema:0.15, vol:0.1, fr:0.1, ob:0.15, bb:0.1, adx:0.2
    # Trending: ema:0.25, adx:0.25, rsi:0.1, bb:0.05 (vol:0.1, fr:0.1, ob:0.15)
    # Ranging: rsi:0.3, bb:0.25, ema:0.05, adx:0.05 (vol:0.1, fr:0.1, ob:0.15)
    # Volatile: vol:0.25, ob:0.2, fr:0.15, rsi:0.1, adx:0.05 (ema:0.15, bb:0.1)

    score_default = engine.get_prediction_score(features, "LONG", regime="UNKNOWN")
    score_trending = engine.get_prediction_score(features, "LONG", regime="TRENDING")
    score_ranging = engine.get_prediction_score(features, "LONG", regime="RANGING")
    score_volatile = engine.get_prediction_score(features, "LONG", regime="VOLATILE")

    # Verify scores are different
    assert score_default != score_trending
    assert score_trending != score_ranging
    assert score_ranging != score_volatile

    # Manual calculation check for Trending
    # Trending LONG:
    # norm_rsi: -2.0 * 0.1 = -0.2
    # norm_ema_slope: 2.0 * 0.25 = 0.5
    # norm_volume_spike: 2.0 * 0.1 = 0.2
    # norm_fr_change: -2.0 * 0.1 = -0.2
    # norm_ob_imbalance: 2.0 * 0.15 = 0.3
    # norm_bb_pct: -2.0 * 0.05 = -0.1
    # norm_adx: 2.0 * 0.25 = 0.5
    # Total: -0.2 + 0.5 + 0.2 - 0.2 + 0.3 - 0.1 + 0.5 = 1.0
    assert pytest.approx(score_trending, 0.01) == 1.0


def test_prediction_engine_short_direction():
    engine = PredictionEngine()
    engine.model = None  # Force heuristic path
    features = {"norm_rsi": 2.0}  # Overbought

    # For SHORT, higher RSI is bearish -> positive score contribution
    # norm_rsi (2.0) * short_direction (1) * weight (0.2) = 0.4
    score_short = engine.get_prediction_score(features, "SHORT", regime="UNKNOWN")
    assert pytest.approx(score_short, 0.01) == 0.4

    # For LONG, higher RSI is bearish -> negative score contribution
    # norm_rsi (2.0) * long_direction (-1) * weight (0.2) = -0.4
    score_long = engine.get_prediction_score(features, "LONG", regime="UNKNOWN")
    assert pytest.approx(score_long, 0.01) == -0.4
