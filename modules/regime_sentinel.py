"""Lightweight sentinel for market regime changes (Steps 22--24).

This module will eventually be responsible for watching incoming price/indicator
streams and deciding whether we have transitioned between trending/ranging/
volatile states.  At the moment it is just a placeholder with the public
interface described in the roadmap; all logic is marked TODO.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class RegimeSentinel:
    def __init__(self, alert_threshold: float = 0.5):
        """Create a sentinel.

        ``alert_threshold`` could represent whatever sensitivity the regime
        detector should use when deciding to fire an alert.  The value is
        purely illustrative until the underlying algorithm is added.
        """
        self.alert_threshold = alert_threshold
        self.current_regime: Optional[str] = None
        # TODO: maintain history of recent regimes / indicator values

    def update(self, price: float, indicators: Dict[str, float]) -> None:
        """Feed a new data point into the sentinel.

        ``price`` is the latest market price and ``indicators`` is an arbitrary
        dictionary (e.g. ``{'rsi': 45.2, 'adx': 23.1}``).  The sentinel should
        buffer the inputs and recompute the current regime when enough data
        accumulates.

        TODO: implement regime detection logic and set ``self.current_regime``.
        """
        # placeholder implementation
        self.current_regime = self.current_regime or "UNKNOWN"
        # TODO: analyze inputs and change regime if warranted

    def is_alert(self) -> bool:
        """Return True if the sentinel has detected a regime change worthy of
        triggering an external notification or trade adjustment.

        TODO: base decision on self.current_regime history and alert_threshold.
        """
        return False


# module-level singleton if desired
regime_sentinel = RegimeSentinel()
