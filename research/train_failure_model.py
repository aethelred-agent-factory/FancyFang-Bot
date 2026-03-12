#!/usr/bin/env python3
import os
import sys
import pandas as pd
import numpy as np
import xgboost as xgb
import joblib
import logging
from pathlib import Path
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import classification_report, accuracy_score, confusion_matrix

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("train_failure_model")

def train_failure_model(data_csv: Path, model_dir: Path):
    if not data_csv.exists():
        logger.error(f"Training data not found at {data_csv}")
        return

    logger.info(f"Loading data from {data_csv}...")
    df = pd.read_csv(data_csv)
    
    # Filter for trades that HAVE a failure_mode or are wins (NONE)
    # The narrator should set 'failure_mode' to NULL for wins, so fillna('NONE')
    df['failure_mode_target'] = df['failure_mode'].fillna('NONE')
    
    if len(df) < 50:
        logger.warning(f"Very small dataset ({len(df)} rows). Results will be noisy.")
        if len(df) < 10:
            logger.error("Not enough data to train. Need at least 10 rows.")
            return

    # Sort by timestamp for TimeSeriesSplit
    df = df.sort_values("timestamp")
    
    # Target variable: failure_mode_target (multi-class)
    y = df["failure_mode_target"]
    
    # Features: anything starting with sig_ or ctx_
    feature_cols = [c for c in df.columns if c.startswith("sig_") or c.startswith("ctx_")]
    X = df[feature_cols]

    # Map target strings to integers for XGBoost
    unique_modes = sorted(y.unique().tolist())
    mode_to_idx = {mode: i for i, mode in enumerate(unique_modes)}
    y_encoded = y.map(mode_to_idx)

    logger.info(f"Training on {len(X)} samples with {len(feature_cols)} features across {len(unique_modes)} failure mode classes.")
    logger.info(f"Classes: {unique_modes}")

    # Final train on all data for the multi-class model
    final_model = xgb.XGBClassifier(
        objective='multi:softprob',
        num_class=len(unique_modes),
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42
    )
    final_model.fit(X, y_encoded)

    # Save model and feature names
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(final_model, model_dir / "failure_mode_model.pkl")
    # We also save the class mapping since XGBoost doesn't store strings by default
    joblib.dump(unique_modes, model_dir / "failure_mode_classes.pkl")
    
    logger.info(f"Failure mode model saved to {model_dir}")
    
    # Feature importance
    importances = pd.Series(final_model.feature_importances_, index=feature_cols).sort_values(ascending=False)
    logger.info("
Top 10 Feature Importances:
" + importances.head(10).to_string())

if __name__ == "__main__":
    SCRIPT_DIR = Path(__file__).parent
    DATA_CSV = SCRIPT_DIR.parent / "data" / "training_data.csv"
    MODEL_DIR = SCRIPT_DIR.parent / "models"
    
    train_failure_model(DATA_CSV, MODEL_DIR)
