"""Backtest stats by day of week - all 5 pairs, 3 months."""
import sys, os
sys.path.insert(0, '/app')
from dotenv import load_dotenv
load_dotenv('/app/.env')
from datetime import timedelta
import pandas as pd
from core.bybit_client import BybitClient
from main import SignalGenerator
from backtest import run_backtest

client = BybitClient(os.getenv('BYBIT_API_KEY'), os.getenv('BYBIT_API_SECRET'), testnet=False, demo=True)

pairs = {
    'BNBUSDT':    {'lookback': 12, 'sweep_threshold': 0.008, 'center_proximity': 0.012, 'tp_multiplier': 1.0, 'timeout': 15, 'adx_min': 25},
    'RENDERUSDT': {'lookback': 12, 'sweep_threshold': 0.008, 'center_proximity': 0.012, 'tp_multiplier': 1.0, 'timeout': 15, 'adx_min': 25},
    'SOLUSDT':    {'lookback': 12, 'sweep_threshold': 0.008, 'center_proximity': 0.012, 'tp_multiplier': 1.0, 'timeout': 15, 'adx_min': 25},
    'LINKUSDT':   {'lookback': 12, 'sweep_threshold': 0.008, 'center_proximity': 0.012, 'tp_multiplier': 1.0, 'timeout': 15, 'adx_min': 25},
    'DOGEUSDT':   {'lookback': 8,  'sweep_threshold': 0.005, 'center_proximity': 0.012, 'tp_multiplier': 1.0, 'timeout': 15, 'adx_min': 30},
}

day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
day_stats = {d: {'signals': 0, 'trades': 0, 'wins': 0, 'pnl': 0.0} for d in day_names}

for symbol, cfg in pairs.items():
    result = client.get_klines(symbol, interval='240', limit=200)
    list_ = result.get('list', [])
    df = pd.DataFrame(list_, columns=['timestamp','open','high','low','close','volume','turnover'])
    for col in ['open','high','low','close','volume','turnover']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['timestamp'] = pd.to_datetime(pd.to_numeric(df['timestamp']), unit='ms')
    df = df.sort_values('timestamp').reset_index(drop=True)

    cutoff = df['timestamp'].max() - timedelta(weeks=12)

    sig_cfg = {
        'strategy': {k: cfg[k] for k in ['lookback', 'sweep_threshold', 'center_proximity', 'tp_multiplier', 'timeout']},
        'filters': {'adx_filter': {'enabled': True, 'min_adx': cfg['adx_min']}}
    }
    gen = SignalGenerator(sig_cfg)

    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            signals.append(sig)

    signals_3m = [s for s in signals if pd.Timestamp(s['timestamp']) >= cutoff]

    for s in signals_3m:
        ts = pd.Timestamp(s['timestamp'])
        day = day_names[ts.dayofweek]
        day_stats[day]['signals'] += 1

    trades, metrics = run_backtest(df, signals)
    for t in trades:
        if t.get('entry_time') is None:
            continue
        for s in signals_3m:
            if abs(t['entry_price'] - s['entry']) < 0.001:
                ts = pd.Timestamp(s['timestamp'])
                day = day_names[ts.dayofweek]
                day_stats[day]['trades'] += 1
                day_stats[day]['pnl'] += t['pnl']
                if t['pnl'] > 0:
                    day_stats[day]['wins'] += 1
                break

print(f"{'Day':<5} {'Signals':<9} {'Trades':<8} {'WR':<8} {'PnL$':<10}")
print("-" * 40)
for d in day_names:
    s = day_stats[d]
    wr = s['wins'] / s['trades'] * 100 if s['trades'] > 0 else 0
    print(f"{d:<5} {s['signals']:<9} {s['trades']:<8} {wr:.0f}%      ${s['pnl']:.2f}")

total_sig = sum(s['signals'] for s in day_stats.values())
total_tr = sum(s['trades'] for s in day_stats.values())
total_w = sum(s['wins'] for s in day_stats.values())
total_pnl = sum(s['pnl'] for s in day_stats.values())
print("-" * 40)
print(f"{'ALL':<5} {total_sig:<9} {total_tr:<8} {total_w/total_tr*100:.0f}%      ${total_pnl:.2f}" if total_tr > 0 else f"{'ALL':<5} {total_sig:<9} 0")
