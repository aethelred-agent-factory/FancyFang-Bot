import os
import joblib
import logging
from typing import Dict, Any, Tuple

# Add project root to path
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logger = logging.getLogger("failure_guard")
logger.addHandler(logging.NullHandler())

class FailureGuard:
    def __init__(self):
        self.model = None
        self.feature_names = None
        self.classes = None
        
        # Load the failure mode classifier
        model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models", "failure_mode_model.pkl"))
        features_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models", "feature_names.pkl"))
        classes_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models", "failure_mode_classes.pkl"))
        
        if os.path.exists(model_path) and os.path.exists(features_path) and os.path.exists(classes_path):
            try:
                self.model = joblib.load(model_path)
                self.feature_names = joblib.load(features_path)
                self.classes = joblib.load(classes_path)
                logger.info("FailureGuard model and classes loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load FailureGuard model: {e}")
        else:
            logger.warning("FailureGuard model or classes not found. Guard is in bypass mode.")

    def evaluate_candidate(self, features: Dict[str, Any]) -> Tuple[str, str, float]:
        """
        Evaluates a candidate trade setup against known failure modes.
        Returns (decision, reason, prob). 
        Decisions: APPROVE, SUPPRESS, REDUCE_SIZE_50
        """
        if self.model is None or self.feature_names is None or self.classes is None:
            return "APPROVE", "Guard model missing — bypassing check.", 0.0

        try:
            # Construct feature vector X
            X = [features.get(f, 0.0) for f in self.feature_names]
            
            # Predict probabilities for each failure mode class
            probs = self.model.predict_proba([X])[0]
            
            # Find the top failure mode (excluding 'NONE' if possible, or just the top one)
            top_idx = probs.argmax()
            top_mode = self.classes[top_idx]
            top_prob = probs[top_idx]
            
            if top_mode == "NONE":
                return "APPROVE", "No significant failure mode detected.", top_prob
            
            if top_prob > 0.80:
                return "SUPPRESS", f"Strong resemblance to historical failure mode: {top_mode} ({top_prob:.1f})", top_prob
            elif top_prob > 0.65:
                return "REDUCE_SIZE_50", f"Resemblance to historical failure mode: {top_mode} ({top_prob:.1f})", top_prob
            
            return "APPROVE", f"Minor resemblance to {top_mode} ({top_prob:.1f}), within tolerance.", top_prob
            
        except Exception as e:
            logger.error(f"FailureGuard evaluation error: {e}")
            return "APPROVE", f"Guard error: {e}", 0.0

# Global instance
failure_guard = FailureGuard()
