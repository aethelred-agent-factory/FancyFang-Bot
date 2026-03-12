import hashlib
import hmac
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("PHEMEX_BASE_URL", "https://api.phemex.com")
API_KEY = os.getenv("PHEMEX_API_KEY")
API_SECRET = os.getenv("PHEMEX_API_SECRET")

if not API_KEY or not API_SECRET:
    print("API keys not found in .env. Skipping exchange-side closure.")
    exit(0)


def send_request(method, endpoint, params=None, body=None):
    expiry = str(int(time.time()) + 60)
    msg = endpoint + expiry + (body if body else "")
    signature = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

    headers = {
        "x-phemex-access-token": API_KEY,
        "x-phemex-request-expiry": expiry,
        "x-phemex-request-signature": signature,
        "Content-Type": "application/json",
    }

    url = BASE_URL + endpoint
    if method == "GET":
        return requests.get(url, headers=headers, params=params)
    elif method == "DELETE":
        return requests.delete(url, headers=headers, params=params)
    else:
        return requests.post(url, headers=headers, data=body)


print("--- CLOSING ALL LIVE TRADES & ORDERS ON PHEMEX ---")

# 1. Cancel all active orders
print("Cancelling all active orders...")
resp = send_request(
    "DELETE", "/orders/all", params={"symbol": "BTCUSDT"}
)  # Phemex often requires a symbol but can cancel all
if resp.status_code == 200:
    print("Orders cancelled successfully.")
else:
    print(f"Failed to cancel orders: {resp.text}")

# 2. Get all positions
print("Fetching open positions...")
resp = send_request("GET", "/accounts/accountPositions", params={"currency": "USDT"})
if resp.status_code == 200:
    data = resp.json()
    positions = data.get("data", {}).get("positions", [])
    open_pos = [p for p in positions if float(p.get("size", 0)) != 0]

    if not open_pos:
        print("No open positions found.")
    else:
        for pos in open_pos:
            symbol = pos["symbol"]
            side = "Sell" if pos["side"] == "Buy" else "Buy"
            size = abs(float(pos["size"]))
            print(f"Closing {side} position for {symbol} (size: {size})...")

            # Market close order
            close_body = f'{{"symbol":"{symbol}","side":"{side}","orderQty":{size},"ordType":"Market","reduceOnly":true}}'
            close_resp = send_request("POST", "/orders", body=close_body)
            if close_resp.status_code == 200:
                print(f"Successfully closed {symbol}.")
            else:
                print(f"Failed to close {symbol}: {close_resp.text}")
else:
    print(f"Failed to fetch positions: {resp.text}")

print("--- FINISHED ---")
