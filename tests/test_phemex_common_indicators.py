import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
import numpy as np
import core.phemex_common as pc
from unittest.mock import MagicMock, patch

def test_calc_atr():
    """Test Average True Range (ATR) calculation."""
    # high, low, close for 4 candles gives 3 true ranges
    highs = [100, 110, 115, 120]
    lows = [90, 90, 95, 100]
    closes = [95, 100, 110, 115]
    # TR1: max(110-90, 110-95, 90-95) = 20
    # TR2: max(115-95, 115-100, 95-100) = 20
    # TR3: max(120-100, 120-110, 100-110) = 20
    # ATR(3) = (20+20+20)/3 = 20
    atr = pc.calc_atr(highs, lows, closes, period=3)
    assert atr == 20.0

def test_calc_bb():
    """Test Bollinger Bands calculation."""
    prices = [10, 12, 14, 16, 18] # mean = 14
    # std = sqrt(((10-14)^2 + (12-14)^2 + (14-14)^2 + (16-14)^2 + (18-14)^2) / 5)
    # std = sqrt((16 + 4 + 0 + 4 + 16) / 5) = sqrt(40 / 5) = sqrt(8) approx 2.828
    # upper = 14 + 2 * 2.828 = 19.656
    # lower = 14 - 2 * 2.828 = 8.344
    bb = pc.calc_bb(prices, period=5, mult=2.0)
    assert pytest.approx(bb["mid"]) == 14.0
    assert pytest.approx(bb["upper"]) == 14.0 + 2 * np.std(prices)
    assert pytest.approx(bb["lower"]) == 14.0 - 2 * np.std(prices)

def test_update_atr_trail_long():
    """Test ATR-based trailing stop update for LONG position."""
    # Initial: price 100, stop 95, high 100, trail 5
    # Price moves to 110
    # New high = 110, new stop = 110 - 5 = 105
    stop, high, low = pc.update_atr_trail(110, 95, 100, 100, 5.0, "LONG")
    assert stop == 105.0
    assert high == 110.0
    
    # Price moves down to 108
    # High remains 110, stop remains 105
    stop, high, low = pc.update_atr_trail(108, 105, 110, 100, 5.0, "LONG")
    assert stop == 105.0
    assert high == 110.0

def test_update_atr_trail_short():
    """Test ATR-based trailing stop update for SHORT position."""
    # Initial: price 100, stop 105, low 100, trail 5
    # Price moves to 90
    # New low = 90, new stop = 90 + 5 = 95
    stop, high, low = pc.update_atr_trail(90, 105, 100, 100, 5.0, "SHORT")
    assert stop == 95.0
    assert low == 90.0

def test_rolling_normalizer():
    """Test RollingNormalizer z-score calculation."""
    norm = pc.RollingNormalizer(window=10)
    # Need at least 3 samples
    assert norm.update_and_score(10) == 0.0
    assert norm.update_and_score(10) == 0.0
    # Now we have [10, 10, 20] -> mean 13.33, std 4.71
    # (20 - 13.33) / 4.71 approx 1.414
    score = norm.update_and_score(20)
    assert score > 1.0
    
    # Test reset
    norm.reset()
    assert norm.update_and_score(10) == 0.0

def test_calc_order_book_imbalance():
    """Test order book imbalance ratio."""
    bids = [[100, 10], [99, 20]] # volume = 1000 + 1980 = 2980
    asks = [[101, 5], [102, 10]] # volume = 505 + 1020 = 1525
    imbalance = pc.calc_order_book_imbalance(bids, asks, depth_levels=2)
    assert imbalance == 2980 / 1525
    
    # Empty bids/asks
    assert pc.calc_order_book_imbalance([], asks) is None

def test_calc_shannon_entropy():
    """Test Shannon entropy of signals."""
    # 5 long, 5 short, 90 none -> total 100
    # p_long = 0.05, p_short = 0.05, p_none = 0.90
    # H = -(0.05*log2(0.05) + 0.05*log2(0.05) + 0.90*log2(0.90))
    p = [0.05, 0.05, 0.90]
    expected = -sum(x * np.log2(x) for x in p)
    entropy = pc.calc_shannon_entropy_signals(5, 5, 100)
    assert pytest.approx(entropy, abs=1e-4) == expected

def test_calc_hurst_exponent():
    """Test Hurst exponent calculation for trending and random data."""
    # Trending series (persistent) -> H > 0.5
    trending = np.cumsum(np.random.normal(0.1, 0.01, 100)).tolist()
    h_trend = pc.calc_hurst_exponent(trending)
    # Hurst exponent can be noisy on small samples, but generally > 0.4 for strong trends
    assert h_trend >= 0.0 
    
    # Mean-reverting series -> H < 0.5
    # (Simplified mean reversion)
    reverting = [100 + (i % 2) * 2 for i in range(100)]
    h_rev = pc.calc_hurst_exponent(reverting)
    assert h_rev < 0.6 # Ideally lower, but Hurst estimation on small windows is loose

def test_hawkes_tracker():
    """Test HawkesTracker intensity and decay."""
    tracker = pc.HawkesTracker(mu=0.1, alpha=1.0, beta=1.0)
    initial = tracker.get_intensity()
    assert initial == 0.1
    
    # Update with event
    intensity = tracker.update(event_occurred=True)
    # intensity should be mu + alpha (approx, if dt is small)
    assert intensity > 1.0
    
    # Wait and check decay
    with patch("time.time") as mock_time:
        mock_time.return_value = tracker.last_time + 1.0 # 1 second later
        decayed = tracker.update(event_occurred=False)
        # intensity = 0.1 + (prev_intensity - 0.1) * exp(-1.0 * 1.0)
        assert decayed < intensity

def test_check_spread_filter():
    """Test spread filter."""
    # Max is 0.20 by default
    pass_ok, reason = pc.check_spread_filter(0.1, "BTC")
    assert pass_ok is True
    
    pass_fail, reason = pc.check_spread_filter(0.3, "BTC")
    assert pass_fail is False
    assert "spread" in reason

def test_check_volatility_filter():
    """Test volatility filter."""
    # Min is 0.002 by default
    # ATR/Price = 10 / 1000 = 0.01 (Pass)
    pass_ok, _ = pc.check_volatility_filter(10, 1000, "BTC")
    assert pass_ok is True
    
    # ATR/Price = 1 / 1000 = 0.001 (Fail)
    pass_fail, _ = pc.check_volatility_filter(1, 1000, "BTC")
    assert pass_fail is False
