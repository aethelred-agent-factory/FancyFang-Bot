
import requests
import json

BASE_URL = "https://api.phemex.com"
symbol = "BTCUSDT"
limit = 5

for res in [14400, "14400", "4H", "4h", 240, "240"]:
    params = {"symbol": symbol, "resolution": res, "limit": limit}
    url = f"{BASE_URL}/exchange/public/md/v2/kline/last"
    print(f"Testing resolution: {res}")
    resp = requests.get(url, params=params)
    print(f"  Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        rows = data.get("data", {}).get("rows", [])
        print(f"  Rows returned: {len(rows)}")
    else:
        print(f"  Error: {resp.text}")
