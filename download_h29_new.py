"""H29: докачка 4H данных для новых кандидатов в пул (Bybit, linear).

Запуск только на VPS: локально из Windows Bybit geo-blocked (403).
    docker exec smc-bot python download_h29_new.py
"""
import sys
import time

import ccxt
import pandas as pd
from pathlib import Path

CACHE = Path(__file__).parent / "data" / "raw"
# TRON торгуется на Bybit как TRX
COINS = ["ONDO", "HYPE", "BNB", "TRX", "RENDER", "UNI", "AVAX"]
SINCE = "2020-01-01T00:00:00Z"


def fetch_ohlcv(exchange, symbol, timeframe="4h", since=SINCE, retries=3):
    cache_file = CACHE / f"{symbol.split('/')[0]}_{timeframe}_{since[:10]}_top20.csv"
    if cache_file.exists():
        df = pd.read_csv(cache_file, parse_dates=["timestamp"])
        print(f"  cached: {len(df)} candles")
        return df
    all_candles = []
    since_ms = exchange.parse8601(since)
    while True:
        candles = None
        for attempt in range(retries):
            try:
                candles = exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=1000)
                break
            except Exception as e:
                print(f"  fetch error: {str(e)[:80]}, retry in {7 + attempt * 6}s", flush=True)
                time.sleep(7 + attempt * 6)
        if not candles:
            break
        all_candles.extend(candles)
        since_ms = candles[-1][0] + 1
        if len(candles) < 1000:
            break
        time.sleep(0.2)
    if not all_candles:
        return pd.DataFrame()
    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_localize(None)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_file, index=False)
    return df


def main():
    bybit = ccxt.bybit({"enableRateLimit": True})
    bybit.options["defaultType"] = "linear"
    ok, fail = [], []
    for base in COINS:
        sym = f"{base}/USDT:USDT"
        print(f"=== {sym} ===", flush=True)
        try:
            df = fetch_ohlcv(bybit, sym, "4h")
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {str(e)[:100]}")
            fail.append(sym)
            continue
        if df.empty:
            print("  EMPTY (no market?)")
            fail.append(sym)
            continue
        print(f"  {len(df)} candles  {df['timestamp'].iloc[0].date()} .. {df['timestamp'].iloc[-1].date()}")
        ok.append((sym, len(df), df["timestamp"].iloc[0].date()))

    print("\n=== SUMMARY ===")
    for s, n, d0 in ok:
        print(f"  OK   {s:<20} {n:>6} bars  from {d0}")
    for s in fail:
        print(f"  FAIL {s}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
