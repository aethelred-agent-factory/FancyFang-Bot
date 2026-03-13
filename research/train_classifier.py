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
logger = logging.getLogger("train_classifier")

def train_classifier(data_csv: Path, model_dir: Path):
    if not data_csv.exists():
        logger.error(f"Training data not found at {data_csv}")
        return

    logger.info(f"Loading data from {data_csv}...")
    df = pd.read_csv(data_csv)
    
    if len(df) < 50:
        logger.warning(f"Very small dataset ({len(df)} rows). Results will be noisy.")
        if len(df) < 10:
            logger.error("Not enough data to train. Need at least 10 rows.")
            return

    # Sort by timestamp for TimeSeriesSplit
    df = df.sort_values("timestamp")
    
    # Target variable: is_win (binary)
    y = df["is_win"]
    
    # Features: anything starting with sig_ or ctx_
    feature_cols = [c for c in df.columns if c.startswith("sig_") or c.startswith("ctx_")]
    X = df[feature_cols]

    logger.info(f"Training on {len(X)} samples with {len(feature_cols)} features.")

    # TimeSeriesSplit
    tscv = TimeSeriesSplit(n_splits=5)
    
    fold_scores = []
    for train_index, test_index in tscv.split(X):
        X_train, X_test = X.iloc[train_index], X.iloc[test_index]
        y_train, y_test = y.iloc[train_index], y.iloc[test_index]
        
        model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42
        )
        
        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)
        
        acc = accuracy_score(y_test, y_pred)
        fold_scores.append(acc)
        logger.info(f"Fold accuracy: {acc:.4f}")

    logger.info(f"Mean validation accuracy: {np.mean(fold_scores):.4f}")

    # Final train on all data
    final_model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42
    )
    final_model.fit(X, y)

    # Save model and feature names
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(final_model, model_dir / "xgb_classifier.pkl")
    joblib.dump(feature_cols, model_dir / "feature_names.pkl")
    
    logger.info(f"Model and feature names saved to {model_dir}")
    
    # Feature importance
    importances = pd.Series(final_model.feature_importances_, index=feature_cols).sort_values(ascending=False)
    logger.info("\nTop 10 Feature Importances:\n" + importances.head(10).to_string())

if __name__ == "__main__":
    SCRIPT_DIR = Path(__file__).parent
    DATA_CSV = SCRIPT_DIR.parent / "data" / "training_data.csv"
    MODEL_DIR = SCRIPT_DIR.parent / "models"
    
    train_classifier(DATA_CSV, MODEL_DIR)
