
import requests

BASE_URL = "https://api.phemex.com"
symbol = "TRUMPUSDT"
resolution = 300  # 5m
limit = 500

params = {"symbol": symbol, "resolution": resolution, "limit": limit}
url = f"{BASE_URL}/exchange/public/md/v2/kline/last"

print(f"Requesting: {url} with params {params}")
resp = requests.get(url, params=params)
print(f"Status: {resp.status_code}")
data = resp.json()
rows = data.get("data", {}).get("rows", [])
print(f"Returned {len(rows)} rows")
