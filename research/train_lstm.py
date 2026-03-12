#!/usr/bin/env python3
"""Train an LSTM on the sequence dataset produced by build_sequences.py.

Phase 3 Step 16: simple PyTorch LSTM classifier. The architecture mirrors the
roadmap description: 60 candles by 7 features input, 2-layer LSTM, 64 hidden
units, dropout 0.3, final sigmoid output. TimeSeriesSplit is used to split
along the time axis rather than random shuffling.

The model is saved as a PyTorch state dict under `models/lstm_model.pth`.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import numpy as np
import torch
import torch.nn as nn
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score

# project root
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logger = logging.getLogger("train_lstm")
logging.basicConfig(level=logging.INFO)


class MarketLSTM(nn.Module):
    def __init__(self, input_size: int = 7, hidden_size: int = 64, num_layers: int = 2, dropout: float = 0.3):
        super().__init__()
        self.lstm = nn.LSTM(input_size=input_size, hidden_size=hidden_size,
                            num_layers=num_layers, dropout=dropout,
                            batch_first=True)
        self.fc = nn.Linear(hidden_size, 1)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x shape: (batch, seq_len, input_size)
        out, _ = self.lstm(x)
        out = out[:, -1, :]  # take last time step
        out = self.fc(out)
        return self.sigmoid(out)


def load_data(npz_path: Path):
    data = np.load(npz_path, allow_pickle=True)
    X = data["X"]  # shape (N, L, 7)
    y = data["y"]  # shape (N,)
    return X, y


def train_lstm(npz_file: Path, model_dir: Path, epochs: int = 20, batch_size: int = 32):
    if not npz_file.exists():
        logger.error(f"Sequence dataset not found at {npz_file}")
        return

    X, y = load_data(npz_file)
    n_samples = X.shape[0]
    if n_samples == 0:
        logger.error("Empty dataset")
        return

    logger.info(f"Loaded {n_samples} sequences")

    # time-series cross validation
    tscv = TimeSeriesSplit(n_splits=5)

    best_acc = 0.0
    for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
        logger.info(f"Fold {fold+1}/{tscv.get_n_splits()} training")
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        model = MarketLSTM()
        criterion = nn.BCELoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

        # convert to tensors
        X_train_t = torch.tensor(X_train, dtype=torch.float32)
        y_train_t = torch.tensor(y_train.reshape(-1, 1), dtype=torch.float32)
        X_test_t = torch.tensor(X_test, dtype=torch.float32)
        y_test_t = torch.tensor(y_test.reshape(-1, 1), dtype=torch.float32)

        train_dataset = torch.utils.data.TensorDataset(X_train_t, y_train_t)
        train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=batch_size, shuffle=False)

        for epoch in range(epochs):
            model.train()
            epoch_losses = []
            for bx, by in train_loader:
                optimizer.zero_grad()
                preds = model(bx)
                loss = criterion(preds, by)
                loss.backward()
                optimizer.step()
                epoch_losses.append(loss.item())
            if epoch % 5 == 0:
                logger.info(f" Fold {fold+1} epoch {epoch} loss {np.mean(epoch_losses):.4f}")

        # evaluation
        model.eval()
        with torch.no_grad():
            preds = model(X_test_t).numpy().flatten()
            pred_labels = (preds >= 0.5).astype(int)
        acc = accuracy_score(y_test, pred_labels)
        logger.info(f" Fold {fold+1} validation accuracy {acc:.4f}")
        best_acc = max(best_acc, acc)

    logger.info(f"Best cross-val accuracy: {best_acc:.4f}")

    # final train on all data
    model = MarketLSTM()
    criterion = nn.BCELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    X_all = torch.tensor(X, dtype=torch.float32)
    y_all = torch.tensor(y.reshape(-1, 1), dtype=torch.float32)
    dataset = torch.utils.data.TensorDataset(X_all, y_all)
    loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=False)

    for epoch in range(epochs):
        model.train()
        losses = []
        for bx, by in loader:
            optimizer.zero_grad()
            preds = model(bx)
            loss = criterion(preds, by)
            loss.backward()
            optimizer.step()
            losses.append(loss.item())
        if epoch % 5 == 0:
            logger.info(f"Final train epoch {epoch} loss {np.mean(losses):.4f}")

    model_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), model_dir / "lstm_model.pth")
    logger.info(f"Saved LSTM model to {model_dir / 'lstm_model.pth'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train LSTM on sequence data")
    parser.add_argument("--data", type=Path, required=True, help="Path to .npz sequence dataset")
    parser.add_argument("--model-dir", type=Path, required=True, help="Directory to save model")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    train_lstm(args.data, args.model_dir, args.epochs, args.batch_size)
