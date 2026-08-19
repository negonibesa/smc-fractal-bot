"""Detailed PnL for top 3 DOGE configs."""
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

result = client.get_klines('DOGEUSDT', interval='240', limit=2200)
list_ = result.get('list', [])
df = pd.DataFrame(list_, columns=['timestamp','open','high','low','close','volume','turnover'])
for col in ['open','high','low','close','volume','turnover']:
    df[col] = pd.to_numeric(df[col], errors='coerce')
df['timestamp'] = pd.to_datetime(pd.to_numeric(df['timestamp']), unit='ms')
df = df.sort_values('timestamp').reset_index(drop=True)

configs = [
    {"name": "#1 (PF=4.64)", "lookback": 12, "sweep_threshold": 0.008, "center_proximity": 0.012, "tp_multiplier": 1.0, "timeout": 15, "adx_min": 30},
    {"name": "#3 (PF=3.07)", "lookback": 8, "sweep_threshold": 0.005, "center_proximity": 0.012, "tp_multiplier": 1.0, "timeout": 15, "adx_min": 30},
    {"name": "#5 (PF=5.70)", "lookback": 8, "sweep_threshold": 0.012, "center_proximity": 0.012, "tp_multiplier": 1.0, "timeout": 6, "adx_min": 30},
]

for cfg in configs:
    print(f"\n{'='*70}")
    print(f"  {cfg['name']}")
    print(f"  lb={cfg['lookback']} sw={cfg['sweep_threshold']} prox={cfg['center_proximity']} tp={cfg['tp_multiplier']} tmo={cfg['timeout']} adx={cfg['adx_min']}")
    print(f"{'='*70}")

    sig_cfg = {
        'strategy': {
            'lookback': cfg['lookback'],
            'sweep_threshold': cfg['sweep_threshold'],
            'center_proximity': cfg['center_proximity'],
            'tp_multiplier': cfg['tp_multiplier'],
            'timeout': cfg['timeout'],
        },
        'filters': {'adx_filter': {'enabled': True, 'min_adx': cfg['adx_min']}}
    }

    gen = SignalGenerator(sig_cfg)
    signals = []
    for j in range(len(df)):
        sig = gen.process_candle(df, j)
        if sig:
            sig['index'] = j
            signals.append(sig)

    trades, metrics = run_backtest(df, signals)

    print(f"\n  Summary: trades={metrics['total_trades']} WR={metrics['win_rate']:.0%} PF={metrics['profit_factor']:.2f} ret={metrics['total_return']:.1%} DD={metrics['max_drawdown']:.1%}")
    print(f"\n  {'Dir':<6} {'Entry':<10} {'Exit':<10} {'PnL$':<10} {'Return%':<10} {'Reason':<16}")
    print(f"  {'-'*62}")

    total_pnl = 0
    wins = 0
    losses = 0
    for t in trades:
        pnl = t['pnl']
        ret = t.get('return_pct', 0)
        total_pnl += pnl
        if pnl > 0: wins += 1
        else: losses += 1
        print(f"  {t['direction']:<6} {t['entry_price']:<10.4f} {t['exit_price']:<10.4f} ${pnl:<9.2f} {ret:.2f}%      {t['exit_reason']:<16}")

    print(f"\n  Total PnL: ${total_pnl:.2f} (wins={wins} losses={losses})")
