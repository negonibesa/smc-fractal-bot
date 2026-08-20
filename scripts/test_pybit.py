"""Test with official pybit SDK."""
import os
from dotenv import load_dotenv

load_dotenv()

from pybit.unified_trading import HTTP

session = HTTP(
    testnet=True,
    api_key=os.getenv('BYBIT_API_KEY'),
    api_secret=os.getenv('BYBIT_API_SECRET'),
)

print("=" * 50)
print("  PYBIT SDK TEST")
print("=" * 50)

# 1. Wallet
print("\n[1] Wallet...")
try:
    resp = session.get_wallet_balance(accountType="unified")
    print(f"    retCode: {resp.get('retCode')}")
    print(f"    retMsg: {resp.get('retMsg')}")
    result = resp.get('result', {})
    if result.get('list'):
        acct = result['list'][0]
        print(f"    Total equity: {acct.get('totalEquity')}")
        for coin in acct.get('coin', []):
            print(f"    {coin.get('coin')}: wallet={coin.get('walletBalance')} available={coin.get('availableToWithdraw')}")
except Exception as e:
    print(f"    ERROR: {e}")

# 2. Position
print("\n[2] Position...")
try:
    resp = session.get_positions(category="linear", symbol="BNBUSDT")
    print(f"    retCode: {resp.get('retCode')}")
    for p in resp.get('result', {}).get('list', []):
        print(f"    {p.get('symbol')}: side={p.get('side')} size={p.get('size')}")
except Exception as e:
    print(f"    ERROR: {e}")

# 3. Leverage
print("\n[3] Set leverage 10x...")
try:
    resp = session.set_leverage(category="linear", symbol="BNBUSDT", buyLeverage="10", sellLeverage="10")
    print(f"    retCode: {resp.get('retCode')}")
    print(f"    retMsg: {resp.get('retMsg')}")
except Exception as e:
    print(f"    ERROR: {e}")

print("\n" + "=" * 50)
print("  DONE")
print("=" * 50)
