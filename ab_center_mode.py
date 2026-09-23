"""
A/B test: body center vs high/low center (both using r_multiple TP)
Isolates the effect of (open+close)/2 vs (high+low)/2
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


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None, limit=1000):
    all_candles = []
    if since is None:
        since = exchange.parse8601('2022-01-01T00:00:00Z')
    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        except Exception as e:
            time.sleep(5)
            try:
                candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            except:
                break
        if not candles:
            break
        all_candles.extend(candles)
        since = candles[-1][0] + 1
        if len(candles) < limit:
            break
        time.sleep(0.2)
    if not all_candles:
        return pd.DataFrame()
    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df


def fetch_funding_rates(exchange, symbol):
    all_rates = []
    end_time = None
    while True:
        params = {}
        if end_time is not None:
            params['endTime'] = end_time
        try:
            rates = exchange.fetch_funding_rate_history(symbol, limit=200, params=params)
        except Exception as e:
            time.sleep(3)
            try:
                rates = exchange.fetch_funding_rate_history(symbol, limit=200, params=params)
            except:
                break
        if not rates:
            break
        all_rates.extend(rates)
        end_time = rates[0]['timestamp'] - 1
        if len(rates) < 200:
            break
        time.sleep(0.15)
    if not all_rates:
        return pd.DataFrame(columns=['timestamp', 'rate'])
    df = pd.DataFrame([{
        'timestamp': pd.Timestamp(r['timestamp'], unit='ms'),
        'rate': r['fundingRate']
    } for r in all_rates])
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df


def generate_signals_with_center_mode(df, config, center_mode, symbol):
    """Generate signals with specific center mode."""
    # Monkey-patch find_consolidation_center for this run
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


def run_test(symbol, bybit_symbol):
    print(f"\n{'='*60}")
    print(f"TEST: {symbol}")
    print(f"{'='*60}")
    
    with open('config/settings.yaml', 'r') as f:
        config = yaml.safe_load(f)
    
    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})
    trailing = config.get('trailing', {})
    
    exchange = ccxt.bybit({
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'},
    })
    
    print(f"Fetching {bybit_symbol}...")
    df = fetch_ohlcv(exchange, bybit_symbol)
    if df.empty:
        print("  No data")
        return None
    
    print(f"  {len(df)} candles")
    
    funding = fetch_funding_rates(exchange, bybit_symbol)
    print(f"  {len(funding)} funding records")
    
    results = {}
    
    for center_mode in ['body', 'hl']:
        print(f"\n  Center mode: {center_mode}")
        
        test_config = {
            'strategy': {**config['strategy'], 'tp_mode': 'r_multiple'},
            'filters': config.get('filters', {}),
        }
        
        signals = generate_signals_with_center_mode(df, test_config, center_mode, bybit_symbol)
        print(f"    Signals: {len(signals)}")
        
        if len(signals) < 5:
            print("    Too few signals")
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
        
        print(f"    Trades: {metrics['total_trades']}, PF: {metrics['profit_factor']:.2f}, "
              f"WR: {metrics['win_rate']:.1%}, Return: {metrics['total_return']:.1%}, "
              f"DD: {metrics['max_drawdown']:.1%}")
        print(f"    Exits: {exit_reasons}")
    
    if len(results) == 2:
        print(f"\n  COMPARISON {symbol}:")
        print(f"  {'Metric':<15} {'Body':>10} {'H/L':>10} {'Delta':>10}")
        print(f"  {'-'*50}")
        for m in ['total_trades', 'profit_factor', 'win_rate', 'total_return', 'max_drawdown']:
            v1 = results['body']['metrics'].get(m, 0)
            v2 = results['hl']['metrics'].get(m, 0)
            if m == 'total_trades':
                print(f"  {m:<15} {v1:>10} {v2:>10} {v2-v1:>+10}")
            else:
                print(f"  {m:<15} {v1:>9.2%} {v2:>9.2%} {v2-v1:>+9.2%}")
    
    return results


if __name__ == '__main__':
    print("A/B TEST: Body Center vs H/L Center")
    print("Both using tp_mode=r_multiple")
    print("=" * 60)
    
    for sym, bybit in [('ETH', 'ETH/USDT:USDT'), ('GRAM', 'GRAM/USDT:USDT')]:
        run_test(sym, bybit)
