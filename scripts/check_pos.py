from dotenv import load_dotenv
load_dotenv('.env')
import os, json
from core.bybit_client import BybitClient
client = BybitClient(os.getenv('BYBIT_API_KEY'), os.getenv('BYBIT_API_SECRET'), testnet=False, demo=True)

positions = client.get_positions()
for p in positions:
    print(f'{p["symbol"]}: {p["side"]} size={p["size"]} entry={p["avgPrice"]} pnl={p["unrealisedPnl"]}')

result = client._request("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"}, signed=True)
wallet = result.get('list', [{}])[0]
for coin in wallet.get('coin', []):
    if coin['coin'] == 'USDT':
        print(f"\nUSDT equity: ${float(coin['equity']):.2f}")
        print(f"walletBalance: ${float(coin['walletBalance']):.2f}")
        print(f"unrealisedPnl: ${float(coin['unrealisedPnl']):.2f}")
        print(f"availableToWithdraw: {coin['availableToWithdraw']}")
        print(f"totalPerp: {wallet.get('totalPerp', 'N/A')}")
print(f"\ntotalEquity: ${float(wallet.get('totalEquity', 0)):.2f}")
