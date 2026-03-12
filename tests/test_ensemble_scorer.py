import os
import sys
import unittest
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from modules.ensemble_scorer import EnsembleScorer

class TestEnsembleScorer(unittest.TestCase):
    def test_score_range(self):
        scorer = EnsembleScorer()
        # dummy features must match those expected by the XGB model (or be empty)
        features = {}
        seq = np.zeros((60, 7))
        score = scorer.score(features, seq, direction="LONG", regime="UNKNOWN")
        self.assertGreaterEqual(score, -1.0)
        self.assertLessEqual(score, 1.0)

if __name__ == "__main__":
    unittest.main()
