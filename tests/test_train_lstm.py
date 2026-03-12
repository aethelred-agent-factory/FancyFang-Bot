import os
import sys
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from research.train_lstm import train_lstm

class TestTrainLSTM(unittest.TestCase):
    def setUp(self):
        # create a tiny sequence dataset
        self.tmp_npz = Path("test_seq.npz")
        X = np.random.rand(20, 10, 7).astype(np.float32)
        y = np.random.randint(0, 2, size=(20,)).astype(np.int8)
        np.savez(self.tmp_npz, X=X, y=y)
        self.model_dir = Path("test_models")
        if self.model_dir.exists():
            for f in self.model_dir.iterdir():
                f.unlink()
            self.model_dir.rmdir()

    def tearDown(self):
        if self.tmp_npz.exists():
            self.tmp_npz.unlink()
        if self.model_dir.exists():
            for f in self.model_dir.iterdir():
                f.unlink()
            self.model_dir.rmdir()

    def test_training_runs(self):
        train_lstm(self.tmp_npz, self.model_dir, epochs=2, batch_size=4)
        self.assertTrue((self.model_dir / "lstm_model.pth").exists())

if __name__ == "__main__":
    unittest.main()
