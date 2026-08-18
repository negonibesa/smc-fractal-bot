import time, hmac, hashlib, requests
from urllib.parse import urlencode

API_KEY = 'a157nI8619ajEDxdsG'
API_SECRET = 'jCxiTHRMnXVg4QqztPfRdZfwXfyvQRW6aDuq'
BASE = 'https://api-testnet.bybit.com'

resp = requests.get(f'{BASE}/v5/market/time', timeout=10)
ts = int(resp.json()['result']['timeSecond']) * 1000
recv_window = 50000
params = {'accountType': 'unified', 'timestamp': ts}
param_str = urlencode(sorted(params.items()))
sign_str = f'{ts}{API_KEY}{recv_window}{param_str}'
sign = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()

headers = {
    'X-BAPI-API-KEY': API_KEY,
    'X-BAPI-TIMESTAMP': str(ts),
    'X-BAPI-SIGN': sign,
    'X-BAPI-RECV-WINDOW': str(recv_window),
    'Content-Type': 'application/json',
}
resp = requests.get(f'{BASE}/v5/account/wallet-balance', params=params, headers=headers, timeout=10)
data = resp.json()
print(f"retCode: {data['retCode']} | {data['retMsg']}")
if data['retCode'] == 0 and data['result'].get('list'):
    for acct in data['result']['list']:
        print(f"Account: {acct['accountType']}")
        for coin in acct.get('coin', []):
            eq = coin.get('equity', 0)
            avail = coin.get('availableToWithdraw', 0)
            print(f"  {coin['coin']}: equity={eq} available={avail}")
else:
    print("Full:", data)
