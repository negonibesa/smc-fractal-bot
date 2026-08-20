import os, time, hmac, hashlib, json
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv('BYBIT_API_KEY')
API_SECRET = os.getenv('BYBIT_API_SECRET')
BASE = "https://api-testnet.bybit.com"

print("Key: " + API_KEY[:8] + "...")
print("Secret: " + API_SECRET[:8] + "...")

def sign(params, ts):
    qs = urlencode(sorted(params.items()))
    raw = str(ts) + API_KEY + "10000" + qs
    return hmac.new(API_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()

# Test 1: no accountType param
print("\n[1] Wallet (no accountType)...")
ts = int(time.time() * 1000)
params = {"timestamp": ts, "recv_window": 10000}
sig = sign(params, ts)
headers = {
    "X-BAPI-API-KEY": API_KEY,
    "X-BAPI-TIMESTAMP": str(ts),
    "X-BAPI-SIGN": sig,
    "X-BAPI-RECV-WINDOW": "10000",
}
resp = requests.get(BASE + "/v5/account/wallet-balance", params=params, headers=headers, timeout=10)
data = resp.json()
print("  retCode=" + str(data.get('retCode')) + " retMsg=" + str(data.get('retMsg')))
if data.get('retCode') == 0:
    for item in data.get('result', {}).get('list', []):
        print("  equity=" + str(item.get('totalEquity')))
        for c in item.get('coin', []):
            print("  " + c.get('coin') + "=" + str(c.get('walletBalance')))

# Test 2: Try contract account
print("\n[2] Wallet (contract)...")
ts = int(time.time() * 1000)
params = {"accountType": "contract", "timestamp": ts, "recv_window": 10000}
sig = sign(params, ts)
headers = {
    "X-BAPI-API-KEY": API_KEY,
    "X-BAPI-TIMESTAMP": str(ts),
    "X-BAPI-SIGN": sig,
    "X-BAPI-RECV-WINDOW": "10000",
}
resp = requests.get(BASE + "/v5/account/wallet-balance", params=params, headers=headers, timeout=10)
data = resp.json()
print("  retCode=" + str(data.get('retCode')) + " retMsg=" + str(data.get('retMsg')))

# Test 3: Position
print("\n[3] Position...")
ts = int(time.time() * 1000)
params = {"category": "linear", "symbol": "BNBUSDT", "timestamp": ts, "recv_window": 10000}
sig = sign(params, ts)
headers = {
    "X-BAPI-API-KEY": API_KEY,
    "X-BAPI-TIMESTAMP": str(ts),
    "X-BAPI-SIGN": sig,
    "X-BAPI-RECV-WINDOW": "10000",
}
resp = requests.get(BASE + "/v5/position/list", params=params, headers=headers, timeout=10)
data = resp.json()
print("  retCode=" + str(data.get('retCode')) + " retMsg=" + str(data.get('retMsg')))
if data.get('retCode') == 0:
    for p in data.get('result', {}).get('list', []):
        print("  " + str(p.get('symbol')) + " size=" + str(p.get('size')))

# Test 4: place a tiny order to prove it works
print("\n[4] Set leverage 1x...")
ts = int(time.time() * 1000)
body = {"category": "linear", "symbol": "BNBUSDT", "buyLeverage": "1", "sellLeverage": "1", "timestamp": ts, "recv_window": 10000}
sig = sign(body, ts)
headers = {
    "Content-Type": "application/json",
    "X-BAPI-API-KEY": API_KEY,
    "X-BAPI-TIMESTAMP": str(ts),
    "X-BAPI-SIGN": sig,
    "X-BAPI-RECV-WINDOW": "10000",
}
resp = requests.post(BASE + "/v5/position/set-leverage", json=body, headers=headers, timeout=10)
data = resp.json()
print("  retCode=" + str(data.get('retCode')) + " retMsg=" + str(data.get('retMsg')))
