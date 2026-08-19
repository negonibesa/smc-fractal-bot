"""Quick backtest: last 7 days, all 4 pairs."""
import sys, time, os
sys.path.insert(0, '.')
from datetime import datetime, timedelta, timezone
import pandas as pd
from core.bybit_client import BybitClient
from smc_features import find_consolidation_center, detect_sweep, calculate_adx
from backtest import run_backtest
from dotenv import load_dotenv
load_dotenv()

client = BybitClient(
    os.getenv('BYBIT_API_KEY'), os.getenv('BYBIT_API_SECRET'),
    testnet=False, demo=True
)

pairs = ['BNBUSDT', 'RENDERUSDT', 'SOLUSDT', 'LINKUSDT']

config = {
    'lookback': 12, 'sweep_threshold': 0.008, 'center_proximity': 0.012,
    'tp_multiplier': 1.0, 'timeout': 15,
}
trailing = {'breakeven_at': 0.5, 'trail_activate': 1.0, 'trail_step': 0.5}

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

    # Keep last 7 days only
    cutoff = df['timestamp'].max() - timedelta(days=7)
    df_7d = df[df['timestamp'] >= cutoff].reset_index(drop=True)
    print(f"  Candles (7d): {len(df_7d)} ({df_7d['timestamp'].min()} to {df_7d['timestamp'].max()})")

    # Generate signals using SignalGenerator logic (state machine)
    from main import SignalGenerator
    sig_config = {'strategy': config, 'filters': {'adx_filter': {'enabled': True, 'min_adx': 25}}}
    gen = SignalGenerator(sig_config)

    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            sig['timestamp'] = str(df['timestamp'].iloc[i])
            signals.append(sig)

    print(f"  Signals (full): {len(signals)}")
    
    # Filter signals in last 7 days
    signals_7d = [s for s in signals if pd.Timestamp(s['timestamp']) >= cutoff]
    print(f"  Signals (7d): {len(signals_7d)}")
    for s in signals_7d:
        print(f"    {s['timestamp']} {s['direction']} entry={s['entry']:.4f} sl={s['stop']:.4f} tp={s['tp']:.4f}")

    # Run backtest on full data for context
    trades, metrics = run_backtest(
        df, signals,
        risk_percent=1.0, commission=0.001, slippage=0.0005, stop_buffer=0.002,
        breakeven_at=trailing['breakeven_at'],
        trailing_activate=trailing['trail_activate'],
        trailing_step=trailing['trail_step'],
    )
    
    print(f"\n  === Backtest (full data) ===")
    print(f"  Trades: {metrics['total_trades']}")
    print(f"  Win Rate: {metrics['win_rate']:.0%}")
    print(f"  PF: {metrics['profit_factor']:.2f}")
    print(f"  Return: {metrics['total_return']:.1%}")
    print(f"  Max DD: {metrics['max_drawdown']:.1%}")
    
    # Trades in last 7 days
    trades_7d = [t for t in trades if t['entry_time'] and pd.Timestamp(t['entry_time']) >= cutoff]
    print(f"  Trades (7d): {len(trades_7d)}")
    for t in trades_7d:
        print(f"    {t['entry_time']} {t['direction']} entry={t['entry_price']:.2f} exit={t['exit_price']:.2f} PnL=${t['pnl']:.2f} ({t['exit_reason']})")
    
    time.sleep(0.5)

print(f"\n{'='*50}")
print("SUMMARY")
print(f"{'='*50}")
