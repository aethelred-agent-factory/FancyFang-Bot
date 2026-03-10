
import unittest
from collections import deque
import threading
import time
import requests
import os
import sys

# Add current dir to path to import core
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))
import core.web_bridge as web_bridge

class TestWebBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = 8082
        cls.logs = deque(["Log 1", "Log 2"], maxlen=10)
        cls.thesis = deque(["Thesis 1", "Thesis 2"], maxlen=10)

        # We need a mock state object
        class MockState:
            def __init__(self):
                self.lock = threading.RLock()
                self.balance = 100.0
                self.rolling_stats = {"wins": 1, "losses": 1, "win_pnl": 10.0, "loss_pnl": -5.0}
                self.positions = []
                self.live_prices = {}
                self.entropy_penalty = 0.0

        cls.state = MockState()
        web_bridge.start_bridge_thread(cls.state, cls.logs, port=cls.port)
        web_bridge.inject_thesis(cls.thesis)
        time.sleep(2) # Wait for server to start

    def test_summary_endpoint(self):
        resp = requests.get(f"http://localhost:{self.port}/api/summary")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["balance"], 100.0)
        self.assertEqual(data["stats"]["wins"], 1)

    def test_thesis_endpoint(self):
        resp = requests.get(f"http://localhost:{self.port}/api/thesis")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0], "Thesis 1")
        self.assertEqual(data[1], "Thesis 2")

    def test_logs_endpoint(self):
        resp = requests.get(f"http://localhost:{self.port}/api/logs")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0], "Log 1")

if __name__ == "__main__":
    unittest.main()
