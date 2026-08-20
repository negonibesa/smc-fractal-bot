"""Debug LINK signal."""
import sys, os
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
from core.bybit_client import BybitClient
import pandas as pd
from main import SignalGenerator
from backtest import run_backtest

client = BybitClient(os.getenv('BYBIT_API_KEY'), os.getenv('BYBIT_API_SECRET'), testnet=False, demo=True)
result = client.get_klines('LINKUSDT', interval='240', limit=50)
list_ = result.get('list', [])
df = pd.DataFrame(list_, columns=['timestamp','open','high','low','close','volume','turnover'])
for col in ['open','high','low','close','volume','turnover']:
    df[col] = pd.to_numeric(df[col], errors='coerce')
df['timestamp'] = pd.to_datetime(pd.to_numeric(df['timestamp']), unit='ms')
df = df.sort_values('timestamp').reset_index(drop=True)

cfg = {'strategy': {'lookback':12,'sweep_threshold':0.008,'center_proximity':0.012,'tp_multiplier':1.0,'timeout':15}, 'filters': {'adx_filter': {'enabled': True, 'min_adx': 25}}}
gen = SignalGenerator(cfg)

signals = []
for i in range(len(df)):
    sig = gen.process_candle(df, i)
    if sig:
        sig['index'] = i
        signals.append(sig)
        ts = df['timestamp'].iloc[i]
        print(f"Signal at idx={i} ts={ts} dir={sig['direction']}")
        print(f"  entry={sig['entry']:.4f} stop={sig['stop']:.4f} tp={sig['tp']:.4f}")

print(f"\nTotal signals: {len(signals)}")
print(f"DF length: {len(df)}")

# Run backtest
trades, metrics = run_backtest(df, signals)
print(f"\nBacktest trades: {metrics['total_trades']}")
for t in trades:
    print(f"  {t['entry_time']} {t['direction']} entry={t['entry_price']:.4f} exit={t['exit_price']:.4f} pnl={t['pnl']:.2f}")
