import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import requests
import time
import sys
from pathlib import Path
from backtest import run_backtest
from smc_features import find_consolidation_center, detect_sweep, calculate_adx

DATA_DIR = Path('data/raw')


def download_data(symbol, interval='240', limit=2190):
    csv_path = DATA_DIR / f'{symbol}_4h_12m.csv'
    if csv_path.exists():
        df = pd.read_csv(csv_path)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if len(df) >= 200:
            print(f"  {symbol}: Loaded from cache ({len(df)} candles)", flush=True)
            return df.tail(limit).reset_index(drop=True)

    url = 'https://api.bybit.com/v5/market/kline'
    all_data = []
    end_time = None
    for batch in range(5):
        params = {'category': 'linear', 'symbol': symbol, 'interval': interval, 'limit': 1000}
        if end_time:
            params['end'] = end_time
        try:
            resp = requests.get(url, params=params, timeout=15)
            data = resp.json()
            if data.get('retCode') != 0:
                print(f"  {symbol}: API error: {data.get('retMsg')}", flush=True)
                break
            rows = data['result']['list']
        except Exception as e:
            print(f"  {symbol}: Download error: {e}", flush=True)
            break
        if not rows:
            break
        all_data.extend(rows)
        end_time = int(rows[-1][0]) - 1
        time.sleep(0.2)

    if len(all_data) < 100:
        return None

    df = pd.DataFrame(all_data, columns=['timestamp','open','high','low','close','volume','turnover'])
    for col in ['open','high','low','close','volume','turnover']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['timestamp'] = pd.to_datetime(pd.to_numeric(df['timestamp']), unit='ms')
    df = df.sort_values('timestamp').reset_index(drop=True)
    df.to_csv(csv_path, index=False)
    print(f"  {symbol}: Downloaded {len(df)} candles", flush=True)
    return df.tail(limit).reset_index(drop=True)


def generate_signals(df_4h, params):
    center = find_consolidation_center(df_4h, lookback=params['lookback'])
    sweep = detect_sweep(df_4h, center, threshold=params['sweep_threshold'])
    adx, plus_di, minus_di = calculate_adx(df_4h, period=14)

    signals_list = []
    state = 0; sweep_direction = None; sweep_price = None
    sweep_index = None; center_at_sweep = None

    for i in range(params['lookback'], len(df_4h)):
        current_close = df_4h['close'].iloc[i]
        current_high = df_4h['high'].iloc[i]
        current_low = df_4h['low'].iloc[i]
        c = center.iloc[i] if not pd.isna(center.iloc[i]) else current_close

        has_bullish = sweep['bullish_sweep'].iloc[i] if i < len(sweep['bullish_sweep']) else False
        has_bearish = sweep['bearish_sweep'].iloc[i] if i < len(sweep['bearish_sweep']) else False

        if state == 0:
            if has_bearish:
                state = 1; sweep_direction = 'bearish'; sweep_price = current_high
                sweep_index = i; center_at_sweep = c
            elif has_bullish:
                state = 1; sweep_direction = 'bullish'; sweep_price = current_low
                sweep_index = i; center_at_sweep = c
        elif state == 1:
            if abs(current_close - center_at_sweep) / center_at_sweep < params['center_proximity']:
                skip = False
                current_adx = adx.iloc[i] if not pd.isna(adx.iloc[i]) else 0
                if current_adx < 25:
                    skip = True
                if current_adx > 25:
                    current_plus = plus_di.iloc[i] if not pd.isna(plus_di.iloc[i]) else 0
                    current_minus = minus_di.iloc[i] if not pd.isna(minus_di.iloc[i]) else 0
                    if sweep_direction == 'bearish' and current_minus < current_plus:
                        skip = True
                    if sweep_direction == 'bullish' and current_plus < current_minus:
                        skip = True
                if skip:
                    state = 0; sweep_direction = None; continue

                entry = center_at_sweep
                if sweep_direction == 'bearish':
                    stop = sweep_price * 1.003; risk = abs(entry - stop)
                    tp = entry - risk * params['tp_multiplier']; direction = 'SELL'
                else:
                    stop = sweep_price * 0.997; risk = abs(stop - entry)
                    tp = entry + risk * params['tp_multiplier']; direction = 'BUY'

                signals_list.append({
                    'index': i, 'timestamp': str(df_4h['timestamp'].iloc[i]),
                    'direction': direction, 'entry': entry,
                    'stop': stop, 'tp': tp
                })
                state = 0; sweep_direction = None
            elif i - sweep_index > params['timeout']:
                state = 0; sweep_direction = None

    return signals_list


def run_one(symbol):
    df_4h = download_data(symbol)
    if df_4h is None or len(df_4h) < 200:
        return None

    params = {'lookback': 12, 'sweep_threshold': 0.008, 'center_proximity': 0.012, 'tp_multiplier': 1.0, 'timeout': 15}
    signals = generate_signals(df_4h, params)
    if len(signals) < 3:
        print(f"  {symbol}: Only {len(signals)} signals, skip", flush=True)
        return None

    bt_df = df_4h.iloc[params['lookback']:].copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]

    trades, metrics = run_backtest(bt_df, bs, initial_balance=10000, risk_percent=1.0,
                                    breakeven_at=0.5, trailing_activate=1.0, trailing_step=0.5)
    return {'symbol': symbol, 'trades': trades, 'metrics': metrics}


if __name__ == '__main__':
    TOP_ALTS = [
        'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT',
        'ADAUSDT', 'AVAXUSDT', 'DOTUSDT', 'LINKUSDT', 'MATICUSDT',
        'UNIUSDT', 'LTCUSDT', 'NEARUSDT', 'APTUSDT', 'SUIUSDT',
        'ARBUSDT', 'OPUSDT', 'INJUSDT', 'FILUSDT', 'ATOMUSDT',
        'AAVEUSDT', 'MKRUSDT', 'TRXUSDT', 'TONUSDT', 'PEPEUSDT',
        'WIFUSDT', 'SEIUSDT', 'FETUSDT', 'RENDERUSDT', 'STXUSDT',
    ]

    results = []
    for i, symbol in enumerate(TOP_ALTS):
        print(f"[{i+1}/{len(TOP_ALTS)}] {symbol}...", flush=True)
        try:
            r = run_one(symbol)
            if r:
                results.append(r)
                m = r['metrics']
                print(f"  -> {m['total_trades']} trades WR={m['win_rate']:.0%} PF={m['profit_factor']:.2f} Ret={m['total_return']:+.2%} DD={m['max_drawdown']:.2%}", flush=True)
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)

    print(f"\n{'='*70}")
    print(f"  SUMMARY — SORTED BY RETURN")
    print(f"{'='*70}")
    print(f"  {'Symbol':>12} | {'Trades':>6} | {'WR':>5} | {'PF':>6} | {'Return':>8} | {'MaxDD':>6} | {'Final$':>10}")
    print(f"  {'-'*68}")
    results.sort(key=lambda x: x['metrics']['total_return'], reverse=True)
    for r in results:
        m = r['metrics']
        print(f"  {r['symbol']:>12} | {m['total_trades']:>6} | {m['win_rate']:.0%} | {m['profit_factor']:>6.2f} | {m['total_return']:>+7.2%} | {m['max_drawdown']:>5.2%} | ${m['final_balance']:>9,.2f}")

    returns = [r['metrics']['total_return'] for r in results]
    winners = sum(1 for r in returns if r > 0)
    total = len(results)
    avg_ret = np.mean(returns)
    avg_wr = np.mean([r['metrics']['win_rate'] for r in results])
    good_pf = [r['metrics']['profit_factor'] for r in results if r['metrics']['profit_factor'] < 100]
    avg_pf = np.mean(good_pf) if good_pf else 0

    print(f"\n  --- PORTFOLIO STATS ---")
    print(f"  Coins: {total} | Winners: {winners}/{total} ({winners/total:.0%})")
    print(f"  Avg Return: {avg_ret:+.2%} | Avg WR: {avg_wr:.0%} | Avg PF: {avg_pf:.2f}")
    print(f"  Best: {results[0]['symbol']} ({results[0]['metrics']['total_return']:+.2%})")
    print(f"  Worst: {results[-1]['symbol']} ({results[-1]['metrics']['total_return']:+.2%})")
