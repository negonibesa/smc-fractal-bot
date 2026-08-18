"""Debug Bybit signature."""
import os, json
import requests, time, hmac, hashlib
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('BYBIT_API_KEY')
API_SECRET = os.getenv('BYBIT_API_SECRET')
BASE_URL = "https://api-testnet.bybit.com"

timestamp = int(time.time() * 1000)
params = {"accountType": "unified", "coin": "USDT", "timestamp": timestamp, "recv_window": 10000}

# Bybit V5 sign: sort params, concat, HMAC-SHA256
param_str = urlencode(sorted(params.items()))
sign_str = f"{API_KEY}{timestamp}{API_SECRET}{param_str}"
signature = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()

print(f"sign_str: {sign_str}")
print(f"signature: {signature}")

headers = {
    "Content-Type": "application/json",
    "X-BAPI-API-KEY": API_KEY,
    "X-BAPI-TIMESTAMP": str(timestamp),
    "X-BAPI-SIGN": signature,
    "X-BAPI-RECV-WINDOW": "10000",
}

url = f"{BASE_URL}/v5/account/wallet-balance"
resp = requests.get(url, params=params, headers=headers, timeout=10)
print(f"\nStatus: {resp.status_code}")
data = resp.json()
print(f"retCode: {data.get('retCode')}")
print(f"retMsg: {data.get('retMsg')}")
if data.get('retCode') == 0:
    result = data.get('result', {})
    print(f"Result: {json.dumps(result, indent=2)[:500]}")
