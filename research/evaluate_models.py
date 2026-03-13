#!/usr/bin/env python3
"""Quick evaluation harness for sequence models and foundation transformers.

Loads the numpy archive created by ``build_sequences.py`` and exercises two
prediction pipelines:

* the local LSTM/XGBoost ensemble via :mod:`modules.ensemble_scorer`
* an off-the-shelf transformer using HuggingFace's `pipeline` API as a
  zero-shot baseline (treats numeric data as text tokens).  The example
  below uses a sentiment model just to get a probability output; the exact
  choice of model isn't important for this prototype.

This script is intentionally minimal; its goal is to give the developer a
fast way to eyeball whether the foundation model is doing anything useful
relative to the existing LSTM.  It is **not** production quality.

Usage::

    python research/evaluate_models.py data/sequences.npz

"""

from __future__ import annotations

import sys
import json
from pathlib import Path
from typing import Any

import numpy as np

# add project root so we can import modules
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from modules import ensemble_scorer


def main(npz_path: Path):
    if not npz_path.exists():
        print(f"File not found: {npz_path}")
        sys.exit(1)

    data = np.load(npz_path)
    # expect keys X,y,trade_ids,symbols as documented by build_sequences
    X = data.get("X")
    y = data.get("y")
    print(f"Loaded {len(X) if X is not None else 0} sequences")

    # prepare a trivial transformer pipeline if available
    try:
        from transformers import pipeline
        text_pipe = pipeline("sentiment-analysis")
    except Exception as exc:
        print("Transformer pipeline unavailable (install `transformers`).")
        text_pipe = None

    # iterate over a handful of examples
    for idx in range(min(5, len(X) if X is not None else 0)):
        seq = X[idx]
        # convert to float list for text model
        seq_flat = " ".join(str(x) for x in seq.flatten().tolist()[:100])
        tf_pred: Any = None
        if text_pipe is not None:
            try:
                tf_pred = text_pipe(seq_flat)[0]
            except Exception as exc:
                tf_pred = {"label": "ERR", "score": 0.0}

        lstm_score = ensemble_scorer.ensemble_scorer.score({}, seq, direction="LONG", regime="UNKNOWN")

        print(f"Example {idx}")
        if tf_pred is not None:
            print(f"  transformer -> {tf_pred}")
        print(f"  lstm/ensemble -> {lstm_score:.4f}\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(Path(sys.argv[1]))
