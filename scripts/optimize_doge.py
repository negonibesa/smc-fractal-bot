"""Fast grid search for DOGEUSDT (12 months) - reduced grid."""
import sys, os, itertools
sys.path.insert(0, '/app')
from dotenv import load_dotenv
load_dotenv('/app/.env')
from datetime import timedelta
import pandas as pd
from core.bybit_client import BybitClient
from main import SignalGenerator
from backtest import run_backtest

client = BybitClient(os.getenv('BYBIT_API_KEY'), os.getenv('BYBIT_API_SECRET'), testnet=False, demo=True)

result = client.get_klines('DOGEUSDT', interval='240', limit=2200)
list_ = result.get('list', [])
df = pd.DataFrame(list_, columns=['timestamp','open','high','low','close','volume','turnover'])
for col in ['open','high','low','close','volume','turnover']:
    df[col] = pd.to_numeric(df[col], errors='coerce')
df['timestamp'] = pd.to_datetime(pd.to_numeric(df['timestamp']), unit='ms')
df = df.sort_values('timestamp').reset_index(drop=True)

cutoff = df['timestamp'].max() - timedelta(weeks=52)
print(f"DOGEUSDT: {len(df[df['timestamp'] >= cutoff])} candles\n")

param_grid = {
    'lookback': [8, 12],
    'sweep_threshold': [0.005, 0.008, 0.012],
    'center_proximity': [0.005, 0.008, 0.012],
    'tp_multiplier': [1.0, 2.0],
    'timeout': [6, 15],
    'adx_min': [20, 30],
}

keys = list(param_grid.keys())
combos = list(itertools.product(*[param_grid[k] for k in keys]))
print(f"Running {len(combos)} combinations...")

results = []
for i, combo in enumerate(combos):
    params = dict(zip(keys, combo))
    sig_cfg = {
        'strategy': {
            'lookback': params['lookback'],
            'sweep_threshold': params['sweep_threshold'],
            'center_proximity': params['center_proximity'],
            'tp_multiplier': params['tp_multiplier'],
            'timeout': params['timeout'],
        },
        'filters': {'adx_filter': {'enabled': True, 'min_adx': params['adx_min']}}
    }
    gen = SignalGenerator(sig_cfg)
    signals = []
    for j in range(len(df)):
        sig = gen.process_candle(df, j)
        if sig:
            sig['index'] = j
            signals.append(sig)
    signals_12m = [s for s in signals if pd.Timestamp(s['timestamp']) >= cutoff]
    if len(signals_12m) < 10:
        continue
    trades, metrics = run_backtest(df, signals)
    if metrics['profit_factor'] < 1.5:
        continue
    results.append({
        'params': params,
        'trades': metrics['total_trades'],
        'wr': metrics['win_rate'],
        'pf': metrics['profit_factor'],
        'ret': metrics['total_return'],
        'dd': metrics['max_drawdown'],
        'score': metrics['profit_factor'] * metrics['total_return'] / max(metrics['max_drawdown'], 0.1),
    })
    if (i + 1) % 50 == 0:
        print(f"  {i+1}/{len(combos)} done, {len(results)} qualifying so far...")

results.sort(key=lambda x: x['score'], reverse=True)
print(f"\nQualifying results (PF>=1.5, min 10 trades): {len(results)}\n")
print(f"{'Rank':<5} {'LB':<4} {'SW':<7} {'PROX':<7} {'TP':<4} {'TMO':<5} {'ADX':<5} {'#':<5} {'WR':<6} {'PF':<7} {'Ret%':<8} {'DD%':<7} {'Score':<7}")
print("-" * 85)
for rank, r in enumerate(results[:20], 1):
    p = r['params']
    print(f"{rank:<5} {p['lookback']:<4} {p['sweep_threshold']:<7} {p['center_proximity']:<7} {p['tp_multiplier']:<4} {p['timeout']:<5} {p['adx_min']:<5} {r['trades']:<5} {r['wr']:.0%}    {r['pf']:.2f}    {r['ret']:.1f}%     {r['dd']:.1f}%   {r['score']:.1f}")

print(f"\nTop 5 detailed:")
for rank, r in enumerate(results[:5], 1):
    print(f"\n#{rank} Score={r['score']:.1f}")
    print(f"  {r['params']}")
    print(f"  Trades={r['trades']} WR={r['wr']:.0%} PF={r['pf']:.2f} Ret={r['ret']:.1f}% DD={r['dd']:.1f}%")
