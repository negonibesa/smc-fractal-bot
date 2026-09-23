"""
A/B test: body center vs high/low center on Binance ETH (proxy)
"""
import sys
import time
import yaml
import ccxt
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backtest import run_backtest
from smc_features import find_consolidation_center
from main import SignalGenerator


def fetch_binance_ohlcv(symbol='ETH/USDT', timeframe='4h', since=None):
    exchange = ccxt.binance({'enableRateLimit': True})
    all_candles = []
    if since is None:
        since = exchange.parse8601('2022-01-01T00:00:00Z')
    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        except Exception as e:
            print(f"  Error: {e}")
            time.sleep(5)
            break
        if not candles:
            break
        all_candles.extend(candles)
        since = candles[-1][0] + 1
        if len(candles) < 1000:
            break
        time.sleep(0.2)
    if not all_candles:
        return pd.DataFrame()
    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df


def generate_signals_with_center_mode(df, config, center_mode, symbol):
    import smc_features as sf
    original_find = sf.find_consolidation_center
    
    def patched_find(df_inner, lookback=20, use_body=True):
        return original_find(df_inner, lookback=lookback, use_body=(center_mode == 'body'))
    
    sf.find_consolidation_center = patched_find
    gen = SignalGenerator(config, symbol=symbol)
    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            sig['timestamp'] = str(df['timestamp'].iloc[i])
            signals.append(sig)
    sf.find_consolidation_center = original_find
    return signals


with open('config/settings.yaml', 'r') as f:
    config = yaml.safe_load(f)

risk_config = config.get('risk', {})
dynamic_risk = config.get('dynamic_risk', {})
trailing = config.get('trailing', {})

print("Fetching Binance ETH/USDT 4H...")
df = fetch_binance_ohlcv('ETH/USDT', '4h')
print(f"Got {len(df)} candles ({df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]})")

# No funding rates for Binance in this test - set to None
funding = None

results = {}
for center_mode in ['body', 'hl']:
    print(f"\nCenter mode: {center_mode}")
    
    test_config = {
        'strategy': {**config['strategy'], 'tp_mode': 'r_multiple'},
        'filters': config.get('filters', {}),
    }
    
    signals = generate_signals_with_center_mode(df, test_config, center_mode, 'ETH/USDT')
    print(f"  Signals: {len(signals)}")
    
    if len(signals) < 5:
        print("  Too few signals")
        continue
    
    bt_df = df.copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]
    
    trades, metrics = run_backtest(
        bt_df, bs,
        initial_balance=10000,
        risk_percent=risk_config.get('risk_percent', 1.0),
        commission=risk_config.get('commission', 0.001),
        slippage=risk_config.get('slippage', 0.0005),
        stop_buffer=risk_config.get('stop_buffer', 0.002),
        breakeven_at=trailing.get('breakeven_at', 0.3),
        trailing_activate=trailing.get('trail_activate', 0.8),
        trailing_step=trailing.get('trail_step', 0.3),
        max_leverage=risk_config.get('max_leverage', 10),
        dynamic_risk=dynamic_risk,
        cooldown_hours=4.0,
        max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
        max_daily_trades=risk_config.get('max_daily_trades', 20),
        max_consecutive_losses=3,
        funding_rates=funding,
    )
    
    exit_reasons = {}
    for t in trades:
        r = t.get('exit_reason', '?')
        exit_reasons[r] = exit_reasons.get(r, 0) + 1
    
    results[center_mode] = {'metrics': metrics, 'exit_reasons': exit_reasons}
    
    print(f"  Trades: {metrics['total_trades']}, PF: {metrics['profit_factor']:.2f}, "
          f"WR: {metrics['win_rate']:.1%}, Return: {metrics['total_return']:.1%}, "
          f"DD: {metrics['max_drawdown']:.1%}")
    print(f"  Exits: {exit_reasons}")

print(f"\n{'='*60}")
print("COMPARISON: Body vs H/L Center (ETH Binance)")
print(f"{'='*60}")
print(f"{'Metric':<20} {'Body':>12} {'H/L':>12} {'Delta':>12}")
print("-" * 60)
for m in ['total_trades', 'profit_factor', 'win_rate', 'total_return', 'max_drawdown']:
    v1 = results['body']['metrics'].get(m, 0)
    v2 = results['hl']['metrics'].get(m, 0)
    if m == 'total_trades':
        print(f"{m:<20} {v1:>12} {v2:>12} {v2-v1:>+12}")
    else:
        print(f"{m:<20} {v1:>11.2%} {v2:>11.2%} {v2-v1:>+11.2%}")
