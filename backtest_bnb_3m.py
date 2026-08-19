"""Backtest BNBUSDT: 3 months."""
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

symbol = 'BNBUSDT'
config = {'lookback': 12, 'sweep_threshold': 0.008, 'center_proximity': 0.012, 'tp_multiplier': 1.0, 'timeout': 15}

result = client.get_klines(symbol, interval='240', limit=500)
list_ = result.get('list', [])
df = pd.DataFrame(list_, columns=['timestamp','open','high','low','close','volume','turnover'])
for col in ['open','high','low','close','volume','turnover']:
    df[col] = pd.to_numeric(df[col], errors='coerce')
df['timestamp'] = pd.to_datetime(pd.to_numeric(df['timestamp']), unit='ms')
df = df.sort_values('timestamp').reset_index(drop=True)

cutoff = df['timestamp'].max() - timedelta(weeks=12)
df_3m = df[df['timestamp'] >= cutoff].reset_index(drop=True)
print(f"Candles (3m): {len(df_3m)} ({df_3m['timestamp'].min()} to {df_3m['timestamp'].max()})")

sig_cfg = {'strategy': config, 'filters': {'adx_filter': {'enabled': True, 'min_adx': 25}}}
gen = SignalGenerator(sig_cfg)

signals = []
for i in range(len(df)):
    sig = gen.process_candle(df, i)
    if sig:
        sig['index'] = i
        signals.append(sig)

signals_3m = [s for s in signals if pd.Timestamp(s['timestamp']) >= cutoff]
print(f"\nSignals (3m): {len(signals_3m)}")
for s in signals_3m:
    print(f"  {s['timestamp']} {s['direction']} entry={s['entry']:.4f} sl={s['stop']:.4f} tp={s['tp']:.4f}")

trades, metrics = run_backtest(df, signals)
print(f"\nBacktest: trades={metrics['total_trades']} WR={metrics['win_rate']:.0%} PF={metrics['profit_factor']:.2f} ret={metrics['total_return']:.1%} DD={metrics['max_drawdown']:.1%}")
for t in trades:
    print(f"  {t['direction']} entry={t['entry_price']:.4f} exit={t['exit_price']:.4f} pnl=${t['pnl']:.2f} ({t['exit_reason']})")
