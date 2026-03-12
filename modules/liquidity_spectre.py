#!/usr/bin/env python3
import logging
from typing import Dict, List

logger = logging.getLogger("liquidity_spectre")


class LiquiditySpectre:
    """
    Advanced order book analysis engine.
    Detects 'Liquidity Walls' and 'Spectre' (spoofing) patterns.
    """

    def __init__(self, wall_threshold: float = 3.0):
        self.wall_threshold = wall_threshold  # Multiple of average depth

    def analyze_book(self, bids: List[List], asks: List[List]) -> Dict[str, any]:
        """
        Analyzes the order book for liquidity anomalies.
        bids/asks format: [[price, size], ...]
        """
        if not bids or not asks:
            return {"spectre_score": 0.0, "walls": []}

        try:
            bid_sizes = [float(b[1]) for b in bids[:20]]
            ask_sizes = [float(a[1]) for a in asks[:20]]

            avg_bid = sum(bid_sizes) / len(bid_sizes) if bid_sizes else 1.0
            avg_ask = sum(ask_sizes) / len(ask_sizes) if ask_sizes else 1.0

            walls = []
            spectre_score = 0.0

            # Detect bid walls
            for i, size in enumerate(bid_sizes):
                if size > avg_bid * self.wall_threshold:
                    walls.append(("BID_WALL", float(bids[i][0]), size / avg_bid))
                    spectre_score += 0.2

            # Detect ask walls
            for i, size in enumerate(ask_sizes):
                if size > avg_ask * self.wall_threshold:
                    walls.append(("ASK_WALL", float(asks[i][0]), size / avg_ask))
                    spectre_score -= 0.2

            return {
                "spectre_score": round(spectre_score, 2),
                "walls": walls,
                "imbalance_v2": (
                    sum(bid_sizes) / sum(ask_sizes) if sum(ask_sizes) > 0 else 1.0
                ),
            }
        except Exception as e:
            logger.error(f"Spectre analysis failed: {e}")
            return {"spectre_score": 0.0, "walls": []}


# Singleton
spectre = LiquiditySpectre()
