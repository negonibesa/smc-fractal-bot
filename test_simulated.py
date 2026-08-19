"""Test Bybit Mainnet SIMULATED mode with time sync."""
import os, sys, time, hmac, hashlib, requests
from urllib.parse import urlencode
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, '.')

key = os.getenv('BYBIT_API_KEY')
secret = os.getenv('BYBIT_API_SECRET')

def get_time_offset(base_url):
    try:
        r = requests.get(f'{base_url}/v5/market/time', timeout=10)
        server = int(r.json()['result']['timeSecond']) * 1000
        local = int(time.time() * 1000)
        return server - local
    except:
        return 0

def make_signed_request(base_url, endpoint, params, offset=0, simulated=False):
    timestamp = int(time.time() * 1000) + offset
    recv_window = 50000
    param_str = urlencode(sorted(params.items()))
    sign_str = f"{timestamp}{key}{recv_window}{param_str}"
    signature = hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()

    headers = {
        'X-BAPI-API-KEY': key,
        'X-BAPI-TIMESTAMP': str(timestamp),
        'X-BAPI-SIGN': signature,
        'X-BAPI-RECV-WINDOW': str(recv_window),
        'Content-Type': 'application/json',
    }
    if simulated:
        headers['X-BAPI-HEADER-SIMULATED'] = '1'

    r = requests.get(f'{base_url}{endpoint}', params=params, headers=headers, timeout=15)
    print(f'  Status: {r.status_code}')
    if r.text:
        print(f'  Body: {r.text[:500]}')
    if r.status_code == 200 and r.text:
        try:
            return r.json()
        except:
            pass
    return None

offset = get_time_offset('https://api.bybit.com')
print(f"Time offset: {offset}ms")

# TEST: Mainnet + SIMULATED
print("\n=== Mainnet + SIMULATED ===")
result = make_signed_request('https://api.bybit.com', '/v5/account/wallet-balance',
                             {'accountType': 'unified', 'coin': 'USDT'}, offset=offset, simulated=True)
if result:
    print(f"retCode: {result.get('retCode')} msg: {result.get('retMsg')}")
    if result.get('retCode') == 0:
        coins = result['result'].get('list', [{}])[0].get('coin', [])
        for c in coins[:5]:
            eq = c.get('equity', '?')
            av = c.get('availableToWithdraw', '?')
            print(f"  {c['coin']}: equity={eq} avail={av}")
