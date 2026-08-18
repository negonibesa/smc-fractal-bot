import requests

# 1. Check our IP
print("[1] My IP...")
try:
    ip = requests.get("https://api.ipify.org", timeout=10).text
    print("  IP: " + ip)
except:
    print("  Cannot get IP")

# 2. Check if Bybit API is reachable at all (public, no auth)
print("\n[2] Bybit public API...")
try:
    resp = requests.get("https://api-testnet.bybit.com/v5/market/tickers?category=linear", timeout=10)
    print("  Status: " + str(resp.status_code))
    print("  Content-Type: " + resp.headers.get("Content-Type", "unknown"))
    text = resp.text[:300]
    print("  Body: " + text)
except Exception as e:
    print("  ERROR: " + str(e)[:200])

# 3. Check mainnet public API too
print("\n[3] Bybit MAINNET public API...")
try:
    resp = requests.get("https://api.bybit.com/v5/market/tickers?category=linear", timeout=10)
    print("  Status: " + str(resp.status_code))
    print("  Content-Type: " + resp.headers.get("Content-Type", "unknown"))
    text = resp.text[:300]
    print("  Body: " + text)
except Exception as e:
    print("  ERROR: " + str(e)[:200])
