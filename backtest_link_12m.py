"""LINKUSDT 12-month detailed backtest."""
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
config = {'lookback': 12, 'sweep_threshold': 0.008, 'center_proximity': 0.012, 'tp_multiplier': 1.0, 'timeout': 15}

result = client.get_klines('LINKUSDT', interval='240', limit=2200)
list_ = result.get('list', [])
df = pd.DataFrame(list_, columns=['timestamp','open','high','low','close','volume','turnover'])
for col in ['open','high','low','close','volume','turnover']:
    df[col] = pd.to_numeric(df[col], errors='coerce')
df['timestamp'] = pd.to_datetime(pd.to_numeric(df['timestamp']), unit='ms')
df = df.sort_values('timestamp').reset_index(drop=True)

cutoff = df['timestamp'].max() - timedelta(weeks=52)
df_12m = df[df['timestamp'] >= cutoff].reset_index(drop=True)
print(f"LINKUSDT: {len(df_12m)} candles ({df_12m['timestamp'].min()} to {df_12m['timestamp'].max()})")

sig_cfg = {'strategy': config, 'filters': {'adx_filter': {'enabled': True, 'min_adx': 25}}}
gen = SignalGenerator(sig_cfg)

signals = []
for i in range(len(df)):
    sig = gen.process_candle(df, i)
    if sig:
        sig['index'] = i
        signals.append(sig)

signals_12m = [s for s in signals if pd.Timestamp(s['timestamp']) >= cutoff]
print(f"\nSignals (12m): {len(signals_12m)}")
for s in signals_12m:
    print(f"  {s['timestamp']} {s['direction']} entry={s['entry']:.4f} sl={s['stop']:.4f} tp={s['tp']:.4f}")

trades, metrics = run_backtest(df, signals)
trades_12m = []
for t in trades:
    if t.get('entry_time') is None:
        continue
    for s in signals_12m:
        if abs(t['entry_price'] - s['entry']) < 0.01:
            trades_12m.append(t)
            break

print(f"\nBacktest (full): trades={metrics['total_trades']} WR={metrics['win_rate']:.0%} PF={metrics['profit_factor']:.2f} ret={metrics['total_return']:.1%} DD={metrics['max_drawdown']:.1%}")
print(f"\nTrades (12m): {len(trades_12m)}")
pnl = 0
wins = 0
losses = 0
for t in trades_12m:
    pnl += t['pnl']
    if t['pnl'] > 0: wins += 1
    else: losses += 1
    print(f"  {t['direction']:<6} entry={t['entry_price']:.4f} exit={t['exit_price']:.4f} pnl=${t['pnl']:.2f} ({t['exit_reason']})")
print(f"\nTotal PnL (12m): ${pnl:.2f} (W={wins} L={losses})")
