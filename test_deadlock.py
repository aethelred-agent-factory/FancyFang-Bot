import threading
import time
from pathlib import Path
from storage_manager import StorageManager
import signal_analytics as analytics
import drawdown_guard
import correlation_manager as corr_mgr
import datetime

def test_init():
    print("Starting init test...")
    db_path = Path("test_deadlock.db")
    if db_path.exists():
        db_path.unlink()
    
    storage = StorageManager(db_path)
    print("StorageManager initialized.")
    
    analytics.init_storage(storage)
    print("Analytics initialized.")
    
    drawdown_guard.init_storage(storage)
    print("DrawdownGuard initialized.")
    
    corr_mgr.init(storage)
    print("CorrelationManager initialized.")
    
    # Simulate stale matrix update
    def _initial_corr_update():
        print("Background update starting...")
        # Just a dummy update that uses storage
        with corr_mgr.correlation_mgr._lock:
            corr_mgr.correlation_mgr.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            corr_mgr.correlation_mgr.save()
        print("Background update complete.")
        
    threading.Thread(target=_initial_corr_update, daemon=True).start()
    time.sleep(1)
    print("Init test complete.")
    
    if db_path.exists():
        db_path.unlink()

if __name__ == "__main__":
    test_init()
