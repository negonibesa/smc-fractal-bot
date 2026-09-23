"""Debug: check structure TP with new relaxed constraints."""
import sys
import yaml
import ccxt
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from smc_features import find_pivot_candle, find_structure_tp, find_consolidation_center
from main import SignalGenerator


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None, limit=1000):
    all_candles = []
    if since is None:
        since = exchange.parse8601('2022-01-01T00:00:00Z')
    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        except Exception as e:
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
signals = []
for i in range(len(df)):
    sig = gen.process_candle(df, i)
    if sig:
        sig['index'] = i
        sig['timestamp'] = str(df['timestamp'].iloc[i])
        signals.append(sig)

print(f"\nFound {len(signals)} signals")
print(f"{'Idx':>5} {'Dir':>5} {'Entry':>10} {'TP':>10} {'StructureTP':>12} {'Used':>8} {'R:R':>6}")
print("-" * 65)

for s in signals:
    idx = s['index']
    direction = s['direction']
    entry = s['entry']
    tp = s['tp']
    stop = s['stop']
    
    structure_tp = find_structure_tp(df, idx, direction, lookback=50)
    risk = abs(entry - stop)
    
    if direction == 'SELL':
        rr = (entry - tp) / risk if risk > 0 else 0
    else:
        rr = (tp - entry) / risk if risk > 0 else 0
    
    used_structure = abs(tp - structure_tp) < 0.0001 if structure_tp else False
    
    print(f"{idx:>5} {direction:>5} {entry:>10.4f} {tp:>10.4f} {structure_tp if structure_tp else 'None':>12} {'YES' if used_structure else 'NO':>8} {rr:>6.1f}R")
