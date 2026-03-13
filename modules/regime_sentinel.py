"""Market regime sentinel for detecting trending/ranging/volatile states.

Monitors price and indicators to identify market regimes and alert on significant changes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from collections import deque


class RegimeSentinel:
    def __init__(self, alert_threshold: float = 0.5, history_length: int = 20):
        """Create a sentinel.

        ``alert_threshold`` is the sensitivity for regime change alerts (0-1).
        ``history_length`` is the number of data points to keep for analysis.
        """
        self.alert_threshold = alert_threshold
        self.history_length = history_length
        self.current_regime: Optional[str] = None
        self.previous_regime: Optional[str] = None
        self.price_history: deque[float] = deque(maxlen=history_length)
        self.indicator_history: deque[Dict[str, float]] = deque(maxlen=history_length)

    def update(self, price: float, indicators: Dict[str, float]) -> None:
        """Feed a new data point into the sentinel.

        Analyzes price and indicators to determine current market regime.
        """
        self.price_history.append(price)
        self.indicator_history.append(indicators.copy())

        if len(self.price_history) < 5:  # Need minimum data
            self.current_regime = "UNKNOWN"
            return

        # Simple regime detection based on RSI and volatility
        rsi = indicators.get('rsi', 50.0)
        volatility = self._calculate_volatility()

        if rsi > 70 and volatility > 0.005:
            new_regime = "BULLISH_TREND"
        elif rsi < 30 and volatility > 0.005:
            new_regime = "BEARISH_TREND"
        elif volatility < 0.005:
            new_regime = "RANGING"
        else:
            new_regime = "VOLATILE"

        self.previous_regime = self.current_regime
        self.current_regime = new_regime

    def _calculate_volatility(self) -> float:
        """Calculate recent price volatility as coefficient of variation."""
        if len(self.price_history) < 2:
            return 0.0
        prices = list(self.price_history)
        mean_price = sum(prices) / len(prices)
        variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
        std_dev = variance ** 0.5
        return std_dev / mean_price if mean_price > 0 else 0.0

    def is_alert(self) -> bool:
        """Return True if a regime change has occurred that warrants an alert."""
        if self.previous_regime is None or self.current_regime == self.previous_regime:
            return False

        # Alert if regime changed significantly (e.g., from trending to ranging)
        significant_changes = [
            ("BULLISH_TREND", "BEARISH_TREND"),
            ("BEARISH_TREND", "BULLISH_TREND"),
            ("RANGING", "BULLISH_TREND"),
            ("RANGING", "BEARISH_TREND"),
            ("RANGING", "VOLATILE"),
            ("VOLATILE", "RANGING"),
            ("BULLISH_TREND", "RANGING"),
            ("BEARISH_TREND", "RANGING"),
            ("BULLISH_TREND", "VOLATILE"),
            ("BEARISH_TREND", "VOLATILE"),
        ]

        return (self.previous_regime, self.current_regime) in significant_changes

    def get_regime_info(self) -> Dict[str, Any]:
        """Return detailed regime information."""
        return {
            "current_regime": self.current_regime,
            "previous_regime": self.previous_regime,
            "alert_triggered": self.is_alert(),
            "history_length": len(self.price_history),
        }


# module-level singleton if desired
regime_sentinel = RegimeSentinel()
