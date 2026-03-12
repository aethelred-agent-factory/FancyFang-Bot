import sqlite3
import sys
from pathlib import Path

db_path = Path('data/state/fancybot.db')
if not db_path.exists():
    print(f"Database not found at {db_path}")
    sys.exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT count(*) FROM trade_history WHERE narrative IS NOT NULL")
count = cursor.fetchone()[0]
conn.close()
print(f"Total annotated trades: {count}")
