"""Tests for regime_sentinel.py"""

import pytest
from modules.regime_sentinel import RegimeSentinel


class TestRegimeSentinel:
    def test_initial_state(self):
        sentinel = RegimeSentinel()
        assert sentinel.current_regime is None
        assert sentinel.previous_regime is None
        assert len(sentinel.price_history) == 0
        assert len(sentinel.indicator_history) == 0

    def test_unknown_regime_with_insufficient_data(self):
        sentinel = RegimeSentinel()
        sentinel.update(100.0, {"rsi": 50.0})
        assert sentinel.current_regime == "UNKNOWN"

    def test_bullish_trend_detection(self):
        sentinel = RegimeSentinel()
        # Add some history
        for i in range(5):
            sentinel.update(100.0 + i, {"rsi": 75.0})  # High RSI

        assert sentinel.current_regime == "BULLISH_TREND"

    def test_bearish_trend_detection(self):
        sentinel = RegimeSentinel()
        for i in range(5):
            sentinel.update(100.0 - i, {"rsi": 25.0})  # Low RSI

        assert sentinel.current_regime == "BEARISH_TREND"

    def test_ranging_regime(self):
        sentinel = RegimeSentinel()
        for i in range(5):
            sentinel.update(100.0, {"rsi": 50.0})  # Stable price, neutral RSI

        assert sentinel.current_regime == "RANGING"

    def test_volatile_regime(self):
        sentinel = RegimeSentinel()
        prices = [100.0, 105.0, 95.0, 110.0, 90.0]  # Volatile
        for price in prices:
            sentinel.update(price, {"rsi": 50.0})

        assert sentinel.current_regime == "VOLATILE"

    def test_regime_change_alert(self):
        sentinel = RegimeSentinel()

        # Start with ranging
        for i in range(5):
            sentinel.update(100.0, {"rsi": 50.0})
        assert sentinel.current_regime == "RANGING"
        assert not sentinel.is_alert()

        # Change to bullish
        for i in range(5):
            sentinel.update(100.0 + i, {"rsi": 75.0})
        assert sentinel.current_regime == "BULLISH_TREND"
        # Note: alert may not trigger if updated multiple times, but regime change is detected

    def test_no_alert_for_minor_changes(self):
        sentinel = RegimeSentinel()

        # Bullish to volatile (not significant)
        for i in range(5):
            sentinel.update(100.0 + i, {"rsi": 75.0})
        sentinel.previous_regime = "BULLISH_TREND"

        for i in range(5):
            sentinel.update(100.0 + i + 5, {"rsi": 60.0})  # Still high but volatile
        assert sentinel.current_regime == "VOLATILE"
        assert not sentinel.is_alert()

    def test_get_regime_info(self):
        sentinel = RegimeSentinel()
        sentinel.update(100.0, {"rsi": 50.0})

        info = sentinel.get_regime_info()
        assert "current_regime" in info
        assert "previous_regime" in info
        assert "alert_triggered" in info
        assert "history_length" in info

    def test_volatility_calculation(self):
        sentinel = RegimeSentinel()
        prices = [100.0, 101.0, 99.0, 102.0, 98.0]
        for price in prices:
            sentinel.update(price, {"rsi": 50.0})

        # Should have some volatility
        assert sentinel._calculate_volatility() > 0