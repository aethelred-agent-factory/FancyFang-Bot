import pytest
from unittest.mock import patch, MagicMock
import phemex_common as pc
import phemex_long
import phemex_short

@pytest.fixture
def mock_ticker():
    return {
        "symbol": "BTCUSDT",
        "lastRp": "100.0",
        "openRp": "90.0",
        "highRp": "110.0",
        "lowRp": "85.0",
        "turnoverRv": "1000000.0"
    }

@pytest.fixture
def mock_candles():
    # API mapping: [timestamp, interval, last, open, high, low, close, volume, ...]
    # We need at least 8 elements
    return [[0, 0, 0, 100, 105, 95, 100, 1000] for _ in range(100)]

@pytest.fixture
def mock_cfg():
    return {
        "TIMEFRAME": "15m",
        "score_threshold": 120,
        "atr_stop_mult": 1.5,
        "atr_trail_mult": 1.0,
        "spread_max_pct": 0.1,
        "vol_min": 0.002
    }

@patch("phemex_common.get_candles")
@patch("phemex_common.get_order_book_with_volumes")
@patch("phemex_common.safe_request")
def test_unified_analyse_long(mock_safe, mock_ob, mock_candles_fn, mock_ticker, mock_candles, mock_cfg):
    """Test unified_analyse for LONG direction."""
    mock_candles_fn.side_effect = lambda symbol, timeframe, limit, rps: mock_candles[:limit]
    mock_ob.return_value = (100.0, 100.1, 0.1, 1000.0, 1.2) # bid, ask, spread, depth, imbalance    
    # Mock safe_request for funding rate and news
    mock_safe.return_value = MagicMock()
    mock_safe.return_value.json.return_value = {"code": 0, "data": {"fundingRate": "0.0001"}}
    
    # We need to ensure score_long returns enough to pass the gate
    # score_long uses indicators calculated from candles.
    # Our flat candles will likely produce a neutral score.
    # Let's mock score_long to return a high score.
    with patch("phemex_long.score_long") as mock_score:
        mock_score.return_value = (150, ["Signal A", "Signal B"])
        
        result = phemex_long.analyse(mock_ticker, mock_cfg)
        
        assert result is not None
        assert result["inst_id"] == "BTCUSDT"
        assert result["direction"] == "LONG"
        assert result["score"] == 150
        assert "Signal A" in result["signals"]
        assert result["confidence"] in ["LOW", "MEDIUM", "HIGH"]

@patch("phemex_common.get_candles")
@patch("phemex_common.get_order_book_with_volumes")
def test_unified_analyse_gate_failure(mock_ob, mock_candles_fn, mock_ticker, mock_candles, mock_cfg):
    """Test that unified_analyse skips expensive calls if pre-score is too low."""
    mock_candles_fn.return_value = mock_candles
    
    # Ensure score_long returns a low score
    with patch("phemex_long.score_long") as mock_score:
        mock_score.return_value = (10, ["Weak Signal"])
        
        result = phemex_long.analyse(mock_ticker, mock_cfg)
        
        assert result is None
        # Verify that get_order_book_with_volumes was NOT called
        assert not mock_ob.called

@patch("phemex_common.get_candles")
def test_unified_analyse_volatility_filter_fail(mock_candles_fn, mock_ticker, mock_candles, mock_cfg):
    """Test volatility filter in unified_analyse."""
    mock_candles_fn.return_value = mock_candles
    
    # Set high volatility filter in cfg
    mock_cfg["vol_min"] = 1.0 # Impossible ATR/Price ratio
    
    # Mock ATR to be low
    with patch("phemex_common.calc_atr", return_value=0.1):
        result = phemex_long.analyse(mock_ticker, mock_cfg)
        assert result is None

@patch("phemex_common.get_candles")
def test_unified_analyse_spread_filter_fail(mock_candles_fn, mock_ticker, mock_candles, mock_cfg):
    """Test spread filter in unified_analyse."""
    mock_candles_fn.return_value = mock_candles
    
    # Mock high spread from ticker
    # spread = (high - low) / last * 100
    mock_ticker["highRp"] = "110.0"
    mock_ticker["lowRp"] = "90.0"
    mock_ticker["lastRp"] = "100.0"
    # spread = 20 / 100 * 100 = 20%
    
    mock_cfg["spread_max_pct"] = 0.1
    
    result = phemex_long.analyse(mock_ticker, mock_cfg)
    assert result is None
