"""Debug Bybit private API."""
import os, json
import requests, time, hmac, hashlib
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('BYBIT_API_KEY')
API_SECRET = os.getenv('BYBIT_API_SECRET')
BASE_URL = "https://api-testnet.bybit.com"

print(f"API_KEY: {API_KEY[:8]}...")
print(f"BASE_URL: {BASE_URL}")

# Manual request
timestamp = int(time.time() * 1000)
params = {"accountType": "unified", "coin": "USDT", "timestamp": timestamp, "recv_window": 5000}

# Sign
param_str = urlencode(sorted(params.items()))
sign_str = f"{API_KEY}{timestamp}{API_KEY}{param_str}"
signature = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()

headers = {
    "Content-Type": "application/json",
    "X-BAPI-API-KEY": API_KEY,
    "X-BAPI-TIMESTAMP": str(timestamp),
    "X-BAPI-SIGN": signature,
    "X-BAPI-RECV-WINDOW": "5000",
}

url = f"{BASE_URL}/v5/account/wallet-balance"
print(f"\nRequest: {url}")
print(f"Params: {params}")

resp = requests.get(url, params=params, headers=headers, timeout=10)
print(f"\nStatus: {resp.status_code}")
print(f"Response: {resp.text[:500]}")
