import os
import joblib
import logging
from typing import Dict, Any, Optional

# Import torch lazily so that projects without it can still import this module
try:
    import torch
    import torch.nn as nn
except ImportError:
    torch = None

# Add project root to path
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from modules.prediction_engine import PredictionEngine

logger = logging.getLogger("ensemble_scorer")
logger.addHandler(logging.NullHandler())


class EnsembleScorer:
    """Combines an XGBoost classifier with a sequence LSTM.

    The XGBoost model is wrapped by :class:`PredictionEngine` and returns a
    score in [-1, +1].  The LSTM yields a win probability in [0,1].  The two
    are blended with fixed weights (60% xgb, 40% lstm by default).  The
    weights can be adjusted later based on validation performance.
    """

    def __init__(self):
        self.xgb_engine = PredictionEngine()
        self.lstm_model: Optional[nn.Module] = None
        self.lstm_device = torch.device("cpu") if torch else None

        # attempt to load LSTM state dict if available
        if torch:
            model_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "models", "lstm_model.pth")
            )
            if os.path.exists(model_path):
                # instantiate same architecture as in train_lstm
                input_size = 7
                hidden_size = 64
                num_layers = 2
                dropout = 0.3
                from research.train_lstm import MarketLSTM

                try:
                    self.lstm_model = MarketLSTM(input_size, hidden_size, num_layers, dropout)
                    self.lstm_model.load_state_dict(torch.load(model_path, map_location=self.lstm_device))
                    self.lstm_model.eval()
                    logger.info("Loaded LSTM ensemble model.")
                except Exception as e:
                    logger.error(f"Failed to load LSTM model: {e}")

    def score(self, features: Dict[str, Any], sequence: Any, direction: str, regime: str) -> float:
        """Compute combined score in [-1, +1].

        Parameters
        ----------
        features: dict
            Feature vector as used by PredictionEngine.
        sequence: array-like
            Sequence of shape (L, 7) representing recent candles.
        direction: str
            "LONG" or "SHORT".  Only used by XGB engine.
        regime: str
            Market regime string passed to XGB engine as well.
        """
        # XGB contribution
        xgb_score = self.xgb_engine.get_prediction_score(features, direction, regime)
        xgb_prob = (xgb_score + 1.0) / 2.0  # map back to probability 0..1

        lstm_prob = 0.5
        if self.lstm_model is not None and torch is not None:
            try:
                seq_tensor = torch.tensor(sequence, dtype=torch.float32).unsqueeze(0)  # (1,L,7)
                with torch.no_grad():
                    lstm_prob = self.lstm_model(seq_tensor).item()
            except Exception as e:
                logger.error(f"LSTM inference error: {e}")

        combined_prob = 0.6 * xgb_prob + 0.4 * lstm_prob
        return (combined_prob - 0.5) * 2.0


# global instance for convenience
ensemble_scorer = EnsembleScorer()
