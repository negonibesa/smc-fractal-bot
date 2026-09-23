"""
A/B test on VPS: structure TP vs r_multiple TP
Runs on VPS where Bybit is accessible
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
from main import SignalGenerator


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None, limit=1000):
    all_candles = []
    if since is None:
        since = exchange.parse8601('2022-01-01T00:00:00Z')
    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        except Exception as e:
            print(f"  Fetch error: {e}, retrying...")
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


def run_ab_test(symbol, bybit_symbol):
    print(f"\n{'='*60}")
    print(f"A/B TEST: {symbol} ({bybit_symbol})")
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
    
    print(f"Fetching data...")
    df = fetch_ohlcv(exchange, bybit_symbol)
    if df.empty:
        print(f"  No data for {bybit_symbol}")
        return None
    
    print(f"  {len(df)} candles ({df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]})")
    
    print(f"  Fetching funding rates...")
    funding = fetch_funding_rates(exchange, bybit_symbol)
    print(f"  {len(funding)} funding records")
    
    results = {}
    
    for tp_mode in ['r_multiple', 'structure']:
        print(f"\n  Running {tp_mode}...")
        
        test_config = {
            'strategy': {**config['strategy'], 'tp_mode': tp_mode},
            'filters': config.get('filters', {}),
        }
        
        gen = SignalGenerator(test_config, symbol=bybit_symbol)
        signals = []
        for i in range(len(df)):
            sig = gen.process_candle(df, i)
            if sig:
                sig['index'] = i
                sig['timestamp'] = str(df['timestamp'].iloc[i])
                signals.append(sig)
        
        print(f"    Signals: {len(signals)}")
        
        if len(signals) < 5:
            print(f"    Too few signals, skipping")
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
        
        results[tp_mode] = {'metrics': metrics, 'exit_reasons': exit_reasons}
        
        print(f"    Trades: {metrics['total_trades']}")
        print(f"    PF: {metrics['profit_factor']:.2f}")
        print(f"    WR: {metrics['win_rate']:.1%}")
        print(f"    Return: {metrics['total_return']:.1%}")
        print(f"    Max DD: {metrics['max_drawdown']:.1%}")
        print(f"    Exits: {exit_reasons}")
    
    return results


def print_comparison(results, symbol):
    if not results or len(results) < 2:
        return
    
    print(f"\n{'='*60}")
    print(f"COMPARISON: {symbol}")
    print(f"{'='*60}")
    
    header = f"{'Metric':<20} {'r_multiple':>12} {'structure':>12} {'Delta':>12}"
    print(header)
    print("-" * 60)
    
    for m in ['total_trades', 'profit_factor', 'win_rate', 'total_return', 'max_drawdown']:
        v1 = results['r_multiple']['metrics'].get(m, 0)
        v2 = results['structure']['metrics'].get(m, 0)
        
        if m == 'total_trades':
            print(f"{m:<20} {v1:>12} {v2:>12} {v2-v1:>+12}")
        else:
            delta = v2 - v1
            print(f"{m:<20} {v1:>11.2%} {v2:>11.2%} {delta:>+11.2%}")
    
    print(f"\nExit Reasons:")
    for mode in ['r_multiple', 'structure']:
        print(f"  {mode}: {results[mode]['exit_reasons']}")


if __name__ == '__main__':
    print("A/B TEST: Structure TP vs R-Multiple TP (VPS)")
    print("Running on VPS where Bybit is accessible")
    print("=" * 60)
    
    symbols = [
        ('ETH', 'ETH/USDT:USDT'),
        ('GRAM', 'GRAM/USDT:USDT'),
    ]
    
    all_results = {}
    for symbol, bybit_symbol in symbols:
        results = run_ab_test(symbol, bybit_symbol)
        if results:
            all_results[symbol] = results
            print_comparison(results, symbol)
    
    # Final summary
    print(f"\n{'='*60}")
    print("FINAL SUMMARY")
    print(f"{'='*60}")
    
    for symbol, results in all_results.items():
        if 'r_multiple' in results and 'structure' in results:
            pf_rm = results['r_multiple']['metrics']['profit_factor']
            pf_st = results['structure']['metrics']['profit_factor']
            ret_rm = results['r_multiple']['metrics']['total_return']
            ret_st = results['structure']['metrics']['total_return']
            
            winner_pf = 'structure' if pf_st > pf_rm else 'r_multiple'
            winner_ret = 'structure' if ret_st > ret_rm else 'r_multiple'
            
            print(f"\n{symbol}:")
            print(f"  PF: r_multiple={pf_rm:.2f} vs structure={pf_st:.2f} -> {winner_pf} wins")
            print(f"  Return: r_multiple={ret_rm:.1%} vs structure={ret_st:.1%} -> {winner_ret} wins")
