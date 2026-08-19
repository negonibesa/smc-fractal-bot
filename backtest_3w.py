"""Backtest: 3 weeks, all 4 pairs. No strategy changes."""
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

pairs = ['BNBUSDT', 'RENDERUSDT', 'SOLUSDT', 'LINKUSDT']
config = {'lookback': 12, 'sweep_threshold': 0.008, 'center_proximity': 0.012, 'tp_multiplier': 1.0, 'timeout': 15}

total_signals = 0
total_trades = 0

for symbol in pairs:
    print(f"\n{'='*50}")
    print(f"  {symbol}")
    print(f"{'='*50}")
    
    result = client.get_klines(symbol, interval='240', limit=150)
    list_ = result.get('list', [])
    df = pd.DataFrame(list_, columns=['timestamp','open','high','low','close','volume','turnover'])
    for col in ['open','high','low','close','volume','turnover']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['timestamp'] = pd.to_datetime(pd.to_numeric(df['timestamp']), unit='ms')
    df = df.sort_values('timestamp').reset_index(drop=True)
    
    cutoff = df['timestamp'].max() - timedelta(weeks=3)
    df_3w = df[df['timestamp'] >= cutoff].reset_index(drop=True)
    print(f"  Candles (3w): {len(df_3w)} ({df_3w['timestamp'].min()} to {df_3w['timestamp'].max()})")
    
    sig_cfg = {'strategy': config, 'filters': {'adx_filter': {'enabled': True, 'min_adx': 25}}}
    gen = SignalGenerator(sig_cfg)
    
    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            signals.append(sig)
    
    signals_3w = [s for s in signals if pd.Timestamp(s['timestamp']) >= cutoff]
    print(f"  Signals (3w): {len(signals_3w)}")
    for s in signals_3w:
        print(f"    {s['timestamp']} {s['direction']} entry={s['entry']:.4f} sl={s['stop']:.4f} tp={s['tp']:.4f}")
    
    trades, metrics = run_backtest(df, signals)
    trades_3w = [t for t in trades if t.get('entry_time') is not None]
    
    # Filter by matching signals in 3w window
    trades_3w = []
    for t in trades:
        for s in signals_3w:
            if t.get('entry_price') and abs(t['entry_price'] - s['entry']) < 0.01:
                trades_3w.append(t)
                break
    
    print(f"\n  Backtest (full): trades={metrics['total_trades']} WR={metrics['win_rate']:.0%} PF={metrics['profit_factor']:.2f} ret={metrics['total_return']:.1%} DD={metrics['max_drawdown']:.1%}")
    print(f"  Trades (3w): {len(trades_3w)}")
    for t in trades_3w:
        print(f"    {t['direction']} entry={t['entry_price']:.4f} exit={t['exit_price']:.4f} pnl=${t['pnl']:.2f} ({t['exit_reason']})")
    
    total_signals += len(signals_3w)
    total_trades += len(trades_3w)

print(f"\n{'='*50}")
print(f"  TOTAL (3 weeks, 4 pairs)")
print(f"{'='*50}")
print(f"  Signals: {total_signals}")
print(f"  Trades: {total_trades}")
print(f"  Avg per week: ~{total_signals/3:.1f} signals, ~{total_trades/3:.1f} trades")
