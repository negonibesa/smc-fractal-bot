"""3-month backtest: all 5 pairs."""
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

total_signals = 0
total_trades = 0
total_pnl = 0
total_wins = 0
total_losses = 0

for symbol, cfg in pairs.items():
    print(f"\n{'='*60}")
    print(f"  {symbol}")
    print(f"{'='*60}")

    result = client.get_klines(symbol, interval='240', limit=200)
    list_ = result.get('list', [])
    df = pd.DataFrame(list_, columns=['timestamp','open','high','low','close','volume','turnover'])
    for col in ['open','high','low','close','volume','turnover']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['timestamp'] = pd.to_datetime(pd.to_numeric(df['timestamp']), unit='ms')
    df = df.sort_values('timestamp').reset_index(drop=True)

    cutoff = df['timestamp'].max() - timedelta(weeks=26)
    print(f"  Candles: {len(df)} | 6m window: {cutoff} to {df['timestamp'].max()}")

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
    print(f"  Signals (6m): {len(signals_3m)}")

    # Run backtest on full df, then match trades to 3m signals by entry price
    trades, metrics = run_backtest(df, signals)

    # Match trades to 3m signals
    trades_3m = []
    for t in trades:
        if t.get('entry_time') is None:
            continue
        for s in signals_3m:
            if abs(t['entry_price'] - s['entry']) < 0.001:
                trades_3m.append(t)
                break

    pnl = sum(t['pnl'] for t in trades_3m)
    wins = sum(1 for t in trades_3m if t['pnl'] > 0)
    losses = sum(1 for t in trades_3m if t['pnl'] <= 0)
    wr = wins / len(trades_3m) * 100 if trades_3m else 0

    print(f"  Trades (6m): {len(trades_3m)} | WR: {wr:.0f}% | PnL: ${pnl:.2f}")
    for t in trades_3m:
        print(f"    {t['direction']:<6} entry={t['entry_price']:.4f} exit={t['exit_price']:.4f} pnl=${t['pnl']:.2f} ({t['exit_reason']})")

    total_signals += len(signals_3m)
    total_trades += len(trades_3m)
    total_pnl += pnl
    total_wins += wins
    total_losses += losses

print(f"\n{'='*60}")
print(f"  TOTAL (6 months, 5 pairs)")
print(f"{'='*60}")
print(f"  Signals: {total_signals}")
print(f"  Trades:  {total_trades} ({total_wins}W / {total_losses}L)")
print(f"  WR:      {total_wins/total_trades*100:.0f}%" if total_trades > 0 else "  WR: 0%")
print(f"  PnL:     ${total_pnl:.2f}")
print(f"  Avg/week: ~{total_trades/12:.1f} trades")
