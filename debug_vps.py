"""Debug: why structure TP is never used on VPS."""
import sys
import yaml
import ccxt
import pandas as pd
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from smc_features import find_pivot_candle, find_structure_tp
from main import SignalGenerator


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None, limit=1000):
    all_candles = []
    if since is None:
        since = exchange.parse8601('2022-01-01T00:00:00Z')
    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        except Exception as e:
            print(f"  Fetch error: {e}, retrying...")
            time.sleep(5)
            try:
                candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            except:
                break
        if not candles:
            break
        all_candles.extend(candles)
        since = candles[-1][0] + 1
        if len(candles) < limit:
            break
        time.sleep(0.2)
    if not all_candles:
        return pd.DataFrame()
    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df


exchange = ccxt.bybit({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'},
})

print("Fetching GRAM data...")
df = fetch_ohlcv(exchange, 'GRAM/USDT:USDT')
print(f"Got {len(df)} candles")

with open('config/settings.yaml', 'r') as f:
    config = yaml.safe_load(f)

test_config = {
    'strategy': {**config['strategy'], 'tp_mode': 'structure'},
    'filters': config.get('filters', {}),
}

gen = SignalGenerator(test_config, symbol='GRAM/USDT:USDT')

struct_used = 0
struct_not_found = 0
struct_wrong_dir = 0
struct_too_close = 0
total = 0

for i in range(len(df)):
    sig = gen.process_candle(df, i)
    if sig:
        total += 1
        idx = i
        direction = sig['direction']
        entry = sig['entry']
        stop = sig['stop']
        tp = sig['tp']
        
        stp = find_structure_tp(df, idx, direction, lookback=50)
        risk = abs(entry - stop)
        
        if stp is None:
            struct_not_found += 1
            reason = 'no_pivot'
        elif direction == 'SELL' and stp >= entry:
            struct_wrong_dir += 1
            reason = f'wrong_dir(stp={stp:.4f} >= entry={entry:.4f})'
        elif direction == 'BUY' and stp <= entry:
            struct_wrong_dir += 1
            reason = f'wrong_dir(stp={stp:.4f} <= entry={entry:.4f})'
        else:
            if direction == 'SELL':
                dist = entry - stp
            else:
                dist = stp - entry
            if dist < risk * 0.3:
                struct_too_close += 1
                reason = f'too_close(dist={dist:.4f} < min={risk*0.3:.4f})'
            else:
                struct_used += 1
                reason = f'USED R:R={dist/risk:.1f}R'
        
        print(f'  {idx:>4} {direction:>4} entry={entry:.4f} tp={tp:.4f} struct_tp={stp} -> {reason}')

print(f'\nSummary: {total} signals')
print(f'  Structure TP used: {struct_used}')
print(f'  No pivot found: {struct_not_found}')
print(f'  Wrong direction: {struct_wrong_dir}')
print(f'  Too close: {struct_too_close}')

# Also check ETH
print("\n\nFetching ETH data...")
df_eth = fetch_ohlcv(exchange, 'ETH/USDT:USDT')
print(f"Got {len(df_eth)} candles")

gen_eth = SignalGenerator(test_config, symbol='ETH/USDT:USDT')

struct_used_eth = 0
struct_not_found_eth = 0
struct_wrong_dir_eth = 0
struct_too_close_eth = 0
total_eth = 0

for i in range(len(df_eth)):
    sig = gen_eth.process_candle(df_eth, i)
    if sig:
        total_eth += 1
        idx = i
        direction = sig['direction']
        entry = sig['entry']
        stp = find_structure_tp(df_eth, idx, direction, lookback=50)
        risk = abs(entry - sig['stop'])
        
        if stp is None:
            struct_not_found_eth += 1
        elif direction == 'SELL' and stp >= entry:
            struct_wrong_dir_eth += 1
        elif direction == 'BUY' and stp <= entry:
            struct_wrong_dir_eth += 1
        else:
            if direction == 'SELL':
                dist = entry - stp
            else:
                dist = stp - entry
            if dist < risk * 0.3:
                struct_too_close_eth += 1
            else:
                struct_used_eth += 1

print(f'\nETH Summary: {total_eth} signals')
print(f'  Structure TP used: {struct_used_eth}')
print(f'  No pivot found: {struct_not_found_eth}')
print(f'  Wrong direction: {struct_wrong_dir_eth}')
print(f'  Too close: {struct_too_close_eth}')
