"""Run 7-day backtest on VPS."""
import sys, os
sys.path.insert(0, '/app')
from dotenv import load_dotenv
load_dotenv('/app/.env')
from datetime import timedelta
import pandas as pd
from core.bybit_client import BybitClient
from smc_features import find_consolidation_center, detect_sweep
from backtest import run_backtest

client = BybitClient(os.getenv('BYBIT_API_KEY'), os.getenv('BYBIT_API_SECRET'), testnet=False, demo=True)

pairs = ['BNBUSDT', 'RENDERUSDT', 'SOLUSDT', 'LINKUSDT']
config = {'lookback': 12, 'sweep_threshold': 0.008, 'center_proximity': 0.012, 'tp_multiplier': 1.0, 'timeout': 15}

for symbol in pairs:
    print(f"\n{'='*50}")
    print(f"  {symbol}")
    print(f"{'='*50}")
    
    result = client.get_klines(symbol, interval='240', limit=50)
    list_ = result.get('list', [])
    df = pd.DataFrame(list_, columns=['timestamp','open','high','low','close','volume','turnover'])
    for col in ['open','high','low','close','volume','turnover']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['timestamp'] = pd.to_datetime(pd.to_numeric(df['timestamp']), unit='ms')
    df = df.sort_values('timestamp').reset_index(drop=True)
    
    cutoff = df['timestamp'].max() - timedelta(days=7)
    df_7d = df[df['timestamp'] >= cutoff].reset_index(drop=True)
    print(f"  Candles (7d): {len(df_7d)} ({df_7d['timestamp'].min()} to {df_7d['timestamp'].max()})")
    
    center = find_consolidation_center(df, lookback=config['lookback'])
    sweep = detect_sweep(df, center, threshold=config['sweep_threshold'])
    
    from main import SignalGenerator
    sig_cfg = {'strategy': config, 'filters': {'adx_filter': {'enabled': True, 'min_adx': 25}}}
    gen = SignalGenerator(sig_cfg)
    
    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            signals.append(sig)
    
    signals_7d = [s for s in signals if pd.Timestamp(s['timestamp']) >= cutoff]
    print(f"  Signals (7d): {len(signals_7d)}")
    for s in signals_7d:
        print(f"    {s['timestamp']} {s['direction']} entry={s['entry']:.4f} sl={s['stop']:.4f} tp={s['tp']:.4f}")
    
    trades, metrics = run_backtest(df, signals)
    trades_7d = [t for t in trades if t['entry_time'] and pd.Timestamp(t['entry_time']) >= cutoff]
    print(f"  Trades (full): {metrics['total_trades']}, Trades (7d): {len(trades_7d)}")
    for t in trades_7d:
        print(f"    {t['entry_time']} {t['direction']} entry={t['entry_price']:.4f} exit={t['exit_price']:.4f} pnl=${t['pnl']:.2f} ({t['exit_reason']})")
    
    if signals:
        print(f"  All signals (full period):")
        for s in signals:
            print(f"    {s['timestamp']} {s['direction']} entry={s['entry']:.4f}")
