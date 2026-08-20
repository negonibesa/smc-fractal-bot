"""Test Bybit API with synced time."""
import os, time, hmac, hashlib, requests
from urllib.parse import urlencode
from dotenv import load_dotenv
load_dotenv()

key = os.getenv('BYBIT_API_KEY')
secret = os.getenv('BYBIT_API_SECRET')

def get_server_offset(base_url):
    """Get time offset between local and server."""
    try:
        r = requests.get(f'{base_url}/v5/market/time', timeout=10)
        server_time = int(int(r.json()['result']['timeSecond']) * 1000)
        local_time = int(time.time() * 1000)
        return server_time - local_time
    except:
        return 0

def sign_and_get(base_url, label, offset=0):
    timestamp = int(time.time() * 1000) + offset
    recv_window = 50000
    params = {'accountType': 'unified', 'coin': 'USDT', 'timestamp': timestamp}
    param_str = urlencode(sorted(params.items()))
    sign_str = f"{timestamp}{key}{recv_window}{param_str}"
    signature = hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()

    headers = {
        'X-BAPI-API-KEY': key,
        'X-BAPI-TIMESTAMP': str(timestamp),
        'X-BAPI-SIGN': signature,
        'X-BAPI-RECV-WINDOW': str(recv_window),
    }
    try:
        r = requests.get(f'{base_url}/v5/account/wallet-balance', params=params, headers=headers, timeout=10)
        data = r.json()
        code = data.get('retCode')
        msg = data.get('retMsg')
        print(f'{label}: code={code} msg={msg}')
        if code == 0 and data.get('result'):
            coins = data['result'].get('list', [{}])[0].get('coin', [])
            for c in coins[:5]:
                print(f'  {c["coin"]}: equity={c.get("equity","?")} avail={c.get("availableToWithdraw","?")}')
        return code
    except Exception as e:
        print(f'{label}: ERROR {e}')
        return -1

# Sync time for each server
mainnet_off = get_server_offset('https://api.bybit.com')
testnet_off = get_server_offset('https://api-testnet.bybit.com')
print(f'Mainnet offset: {mainnet_off}ms')
print(f'Testnet offset: {testnet_off}ms')

sign_and_get('https://api.bybit.com', 'Mainnet', mainnet_off)
sign_and_get('https://api-testnet.bybit.com', 'Testnet', testnet_off)

# Mainnet + SIMULATED
timestamp = int(time.time() * 1000) + mainnet_off
recv_window = 50000
params = {'accountType': 'unified', 'coin': 'USDT', 'timestamp': timestamp}
param_str = urlencode(sorted(params.items()))
sign_str = f"{timestamp}{key}{recv_window}{param_str}"
signature = hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
headers = {
    'X-BAPI-API-KEY': key,
    'X-BAPI-TIMESTAMP': str(timestamp),
    'X-BAPI-SIGN': signature,
    'X-BAPI-RECV-WINDOW': str(recv_window),
    'X-BAPI-HEADER-SIMULATED': '1',
}
try:
    r = requests.get('https://api.bybit.com/v5/account/wallet-balance', params=params, headers=headers, timeout=10)
    data = r.json()
    print(f'Mainnet+SIMULATED: code={data.get("retCode")} msg={data.get("retMsg")}')
    if data.get('result'):
        coins = data['result'].get('list', [{}])[0].get('coin', [])
        for c in coins[:5]:
            print(f'  {c["coin"]}: equity={c.get("equity","?")} avail={c.get("availableToWithdraw","?")}')
except Exception as e:
    print(f'Mainnet+SIMULATED: ERROR {e}')
