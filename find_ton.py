import ccxt
e = ccxt.bybit()
markets = e.load_markets()
for s in markets:
    if 'TON' in s.upper() or 'GRAM' in s.upper():
        print(s)
