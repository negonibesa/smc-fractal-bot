import os, time, hmac, hashlib
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv('BYBIT_API_KEY')
API_SECRET = os.getenv('BYBIT_API_SECRET')

def sign_and_request(method, url, params, recv_window=10000):
    ts = int(time.time() * 1000)
    params['timestamp'] = ts
    
    query_string = urlencode(sorted(params.items()))
    sign_str = str(ts) + API_KEY + str(recv_window) + query_string
    signature = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": str(ts),
        "X-BAPI-SIGN": signature,
        "X-BAPI-RECV-WINDOW": str(recv_window),
    }
    
    resp = requests.get(url, params=params, headers=headers, timeout=10)
    print("  HTTP " + str(resp.status_code))
    print("  Body: [" + resp.text[:500] + "]")

print("Test 1: unified account, testnet")
sign_and_request("GET", "https://api-testnet.bybit.com/v5/account/wallet-balance",
                 {"accountType": "unified"})

print("\nTest 2: no accountType, testnet")
sign_and_request("GET", "https://api-testnet.bybit.com/v5/account/wallet-balance", {})

print("\nTest 3: position, testnet")
sign_and_request("GET", "https://api-testnet.bybit.com/v5/position/list",
                 {"category": "linear", "symbol": "BNBUSDT"})

print("\nTest 4: mainnet wallet")
sign_and_request("GET", "https://api.bybit.com/v5/account/wallet-balance",
                 {"accountType": "unified"})
