
import requests
import json

BASE_URL = "https://api.phemex.com"
symbol = "TRUMPUSDT"
resolution = 14400
limit = 5

params = {"symbol": symbol, "resolution": resolution, "limit": limit}
url = f"{BASE_URL}/exchange/public/md/v2/kline/last"

print(f"Requesting: {url} with params {params}")
resp = requests.get(url, params=params)
print(f"Status: {resp.status_code}")
print(f"Body: {resp.text}")
