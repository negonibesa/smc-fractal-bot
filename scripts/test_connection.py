"""Test Bybit testnet connection."""
import os
from dotenv import load_dotenv
from core import BybitClient

load_dotenv()

client = BybitClient(
    api_key=os.getenv('BYBIT_API_KEY'),
    api_secret=os.getenv('BYBIT_API_SECRET'),
    testnet=True
)

print("=" * 50)
print("  BYBIT TESTNET CONNECTION TEST")
print("=" * 50)

# 1. Public API
print("\n[1] Public API - Ticker...")
try:
    ticker = client.get_ticker('BNBUSDT')
    print(f"    BNBUSDT price: {ticker.get('lastPrice')}")
except Exception as e:
    print(f"    ERROR: {e}")

# 2. Klines
print("\n[2] Public API - Klines...")
try:
    klines = client.get_klines('BNBUSDT', interval='240', limit=5)
    print(f"    Got {len(klines.get('list', []))} candles")
except Exception as e:
    print(f"    ERROR: {e}")

# 3. Wallet (requires auth)
print("\n[3] Private API - Wallet...")
try:
    wallet = client.get_wallet_balance()
    total_eq = wallet.get('totalEquity', 'N/A')
    avail = wallet.get('availableToWithdraw', 'N/A')
    print(f"    Total equity: {total_eq}")
    print(f"    Available: {avail}")
except Exception as e:
    print(f"    ERROR: {e}")

# 4. Position (requires auth)
print("\n[4] Private API - Position...")
try:
    pos = client.get_position('BNBUSDT')
    if pos:
        print(f"    Side: {pos.get('side')} Size: {pos.get('size')}")
    else:
        print("    No position (empty)")
except Exception as e:
    print(f"    ERROR: {e}")

# 5. Instrument info
print("\n[5] Instrument info...")
try:
    inst = client.get_instruments('BNBUSDT')
    lot = inst.get('lotSizeFilter', {})
    price = inst.get('priceFilter', {})
    print(f"    Min qty: {lot.get('minOrderQty')}")
    print(f"    Qty step: {lot.get('qtyStep')}")
    print(f"    Tick size: {price.get('tickSize')}")
    print(f"    Min notional: {lot.get('minNotionalValue')}")
except Exception as e:
    print(f"    ERROR: {e}")

print("\n" + "=" * 50)
print("  DONE")
print("=" * 50)
