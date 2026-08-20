"""
True Rolling Walk-Forward Optimization
Simulates live behavior: train → optimize → test → slide → repeat
Each window: optimize params on train, backtest with optimized params on OOS test.
"""
import sys
import time
import yaml
import itertools
import logging
import ccxt
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from backtest import run_backtest, calculate_backtest_metrics
from main import SignalGenerator

logging.basicConfig(level=logging.WARNING)

PARAM_RANGES = {
    'lookback': [8, 10, 12, 15],
    'sweep_threshold': [0.005, 0.008, 0.010, 0.012],
    'center_proximity': [0.008, 0.010, 0.012, 0.015],
    'tp_multiplier': [1.0, 1.5, 2.0],
    'timeout': [6, 10, 15, 20],
}
TRAILING_RANGES = {
    'breakeven_at': [0.3, 0.5, 0.7],
    'trail_activate': [0.8, 1.0, 1.5],
    'trail_step': [0.3, 0.5, 0.7],
}


def ccxt_symbol(s):
    return f"{s.replace('USDT','')}/USDT:USDT"


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None):
    all_candles = []
    if since is None:
        since = exchange.parse8601('2022-01-01T00:00:00Z')
    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        except Exception as e:
            print(f"  Fetch error: {e}")
            time.sleep(5)
            try:
                candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            except:
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
    df = pd.DataFrame(all_candles, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df


def fetch_funding_rates(exchange, symbol):
    all_rates = []
    end_time = None
    page = 0
    while True:
        page += 1
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
        print(f"    Funding page {page}: {len(rates)} records, total {len(all_rates)}")
        end_time = rates[0]['timestamp'] - 1
        if len(rates) < 200:
            break
        time.sleep(0.15)
    if not all_rates:
        return pd.DataFrame(columns=['timestamp','rate'])
    df = pd.DataFrame([{
        'timestamp': pd.Timestamp(r['timestamp'], unit='ms'),
        'rate': r['fundingRate']
    } for r in all_rates])
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df


def run_backtest_on_segment(df, params, trailing, risk_config, dynamic_risk, funding_rates=None, symbol='BACKTEST'):
    """Generate signals + backtest on a specific df segment."""
    sig_config = {'strategy': params, 'filters': {}}
    gen = SignalGenerator(sig_config, symbol=symbol)
    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            sig['timestamp'] = str(df['timestamp'].iloc[i])
            signals.append(sig)
    if len(signals) < 3:
        return None, []
    bt_df = df.copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]
    if not bs:
        return None, []
    trades, metrics = run_backtest(
        bt_df, bs,
        initial_balance=10000,
        risk_percent=risk_config.get('risk_percent', 1.0),
        commission=risk_config.get('commission', 0.001),
        slippage=risk_config.get('slippage', 0.0005),
        stop_buffer=risk_config.get('stop_buffer', 0.002),
        breakeven_at=trailing.get('breakeven_at', 0.5),
        trailing_activate=trailing.get('trail_activate', 1.0),
        trailing_step=trailing.get('trail_step', 0.5),
        max_leverage=risk_config.get('max_leverage', 10),
        dynamic_risk=dynamic_risk,
        cooldown_hours=4.0,
        max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
        max_daily_trades=risk_config.get('max_daily_trades', 20),
        funding_rates=funding_rates,
    )
    return metrics, trades


def generate_combos(ranges, max_n=40):
    keys = list(ranges.keys())
    values = [ranges[k] for k in keys]
    combos = [dict(zip(keys, c)) for c in itertools.product(*values)]
    if len(combos) > max_n:
        idx = np.linspace(0, len(combos)-1, max_n, dtype=int)
        combos = [combos[i] for i in idx]
    return combos


def optimize_on_train(df_train, current_params, current_trailing, risk_config, dynamic_risk, symbol='BACKTEST'):
    """Find best params on training data using grid search + mini walk-forward."""
    combos = generate_combos(PARAM_RANGES, max_n=30)
    best_pf = 0
    best_params = None
    best_trailing = current_trailing.copy()

    # Mini WF on train: split into 3, test on last 2 OOS segments
    n = len(df_train)
    split_size = n // 3

    for combo in combos:
        test_params = {**current_params, **combo}
        oos_results = []
        for s in range(1, 3):
            seg_start = s * split_size
            seg_end = min((s + 1) * split_size, n)
            seg = df_train.iloc[seg_start:seg_end].copy().reset_index(drop=True)
            if len(seg) < 20:
                continue
            m, _ = run_backtest_on_segment(seg, test_params, current_trailing, risk_config, dynamic_risk, symbol=symbol)
            if m and m['total_trades'] >= 2:
                oos_results.append(m['profit_factor'])
        if not oos_results:
            continue
        avg_pf = np.mean(oos_results)
        min_pf = min(oos_results)
        if avg_pf > best_pf and min_pf >= 1.0:
            best_pf = avg_pf
            best_params = test_params

    if best_params is None:
        return current_params, current_trailing, 0

    # Now optimize trailing with best strategy params
    for tc in generate_combos(TRAILING_RANGES, max_n=10):
        test_trailing = {**current_trailing, **tc}
        oos_results = []
        for s in range(1, 3):
            seg_start = s * split_size
            seg_end = min((s + 1) * split_size, n)
            seg = df_train.iloc[seg_start:seg_end].copy().reset_index(drop=True)
            if len(seg) < 20:
                continue
            m, _ = run_backtest_on_segment(seg, best_params, test_trailing, risk_config, dynamic_risk, symbol=symbol)
            if m and m['total_trades'] >= 2:
                oos_results.append(m['profit_factor'])
        if oos_results:
            avg_pf = np.mean(oos_results)
            if avg_pf > best_pf and min(oos_results) >= 1.0:
                best_pf = avg_pf
                best_trailing = test_trailing

    return best_params, best_trailing, best_pf


def rolling_optimization(df, config, risk_config, dynamic_risk, symbol, funding_rates,
                          train_pct=0.4, step_pct=0.15, n_splits=4):
    """True rolling WF: optimize on train, test on OOS, slide forward."""
    n = len(df)
    window_size = int(n * train_pct)
    step_size = int(n * step_pct)

    current_params = config.get('strategy', {})
    current_trailing = config.get('trailing', {})
    results = []

    for i in range(n_splits):
        start = i * step_size
        train_end = start + window_size
        test_start = train_end
        test_end = min(test_start + step_size, n)

        if test_end <= test_start or test_end - test_start < 20 or train_end > n:
            continue

        train_df = df.iloc[start:train_end].copy().reset_index(drop=True)
        test_df = df.iloc[test_start:test_end].copy().reset_index(drop=True)

        train_start_date = str(train_df['timestamp'].iloc[0])[:10]
        train_end_date = str(train_df['timestamp'].iloc[-1])[:10]
        test_start_date = str(test_df['timestamp'].iloc[0])[:10]
        test_end_date = str(test_df['timestamp'].iloc[-1])[:10]

        # Slice funding for this test window
        test_funding = None
        if funding_rates is not None and not funding_rates.empty:
            test_funding = funding_rates[
                (funding_rates['timestamp'] >= test_df['timestamp'].iloc[0]) &
                (funding_rates['timestamp'] <= test_df['timestamp'].iloc[-1])
            ]
            if test_funding.empty:
                test_funding = None

        print(f"\n  Split {i+1}:")
        print(f"    Train: {train_start_date} to {train_end_date} ({len(train_df)} candles)")
        print(f"    Test:  {test_start_date} to {test_end_date} ({len(test_df)} candles)")

        # Step 1: Optimize on training data
        print(f"    Optimizing on train...")
        t0 = time.time()
        opt_params, opt_trailing, opt_pf = optimize_on_train(
            train_df, current_params, current_trailing, risk_config, dynamic_risk, symbol=symbol
        )
        elapsed = time.time() - t0
        print(f"    Done in {elapsed:.0f}s. Best train PF={opt_pf:.2f}")

        if opt_pf == 0:
            print(f"    No profitable config found on train. Using defaults.")
            opt_params = current_params
            opt_trailing = current_trailing

        # Log param changes
        changed = {k: (current_params.get(k), opt_params.get(k))
                   for k in opt_params if opt_params.get(k) != current_params.get(k)}
        if changed:
            print(f"    Params changed: {changed}")

        # Step 2: Test on OOS with optimized params
        print(f"    Testing on OOS...")
        m, trades = run_backtest_on_segment(
            test_df, opt_params, opt_trailing, risk_config, dynamic_risk,
            funding_rates=test_funding
        )

        if m is None:
            print(f"    No trades on OOS. Skipping.")
            continue

        pf = m['profit_factor']
        wr = m['win_rate']
        ret = m['total_return']
        dd = m['max_drawdown']
        nt = m['total_trades']
        flag = "+" if pf >= 1.2 else ("~" if pf >= 1.0 else "-")

        print(f"    Result: {nt}t PF={pf:.2f} WR={wr:.0%} Ret={ret:+.1%} DD={dd:.1%} {flag}")

        results.append({
            'split': i + 1,
            'train_period': f"{train_start_date} to {train_end_date}",
            'test_period': f"{test_start_date} to {test_end_date}",
            'train_candles': len(train_df),
            'test_candles': len(test_df),
            'opt_pf': opt_pf,
            'opt_params': opt_params.copy(),
            'opt_trailing': opt_trailing.copy(),
            'trades': nt,
            'pf': pf,
            'wr': wr,
            'ret': ret,
            'dd': dd,
        })

        # Update current params for next window (carry forward optimized)
        current_params = opt_params
        current_trailing = opt_trailing

    return results


def main():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})
    exchange = ccxt.bybit({'enableRateLimit': True})

    # Run for 6 best pairs from bulk backtest
    target_pairs = ['BNBUSDT', 'ETHUSDT', 'DOGEUSDT', 'XRPUSDT', 'DOTUSDT', 'RENDERUSDT']
    assets = [a for a in config.get('assets', [])
              if a.get('enabled', True) and a['symbol'] in target_pairs]

    # Add any missing pairs with default config
    existing = {a['symbol'] for a in assets}
    for sym in target_pairs:
        if sym not in existing:
            assets.append({
                'symbol': sym,
                'config': {
                    'strategy': config.get('strategy', {}),
                    'filters': config.get('filters', {}),
                    'trailing': config.get('trailing', {}),
                }
            })

    all_results = {}

    for asset in assets:
        symbol = asset['symbol']
        asset_config = asset.get('config', {})
        full_config = {
            'strategy': {**config.get('strategy', {}), **asset_config.get('strategy', {})},
            'filters': {**config.get('filters', {}), **asset_config.get('filters', {})},
            'trailing': {**config.get('trailing', {}), **asset_config.get('trailing', {})},
        }

        print(f"\n{'='*70}")
        print(f"  TRUE ROLLING WF OPTIMIZATION — {symbol}")
        print(f"{'='*70}")

        df = fetch_ohlcv(exchange, ccxt_symbol(symbol))
        if df.empty:
            print(f"  No data, skipping")
            continue

        years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
        print(f"  {len(df)} candles, {years:.1f}Y")

        print(f"  Fetching funding rates...")
        funding = fetch_funding_rates(exchange, ccxt_symbol(symbol))
        print(f"  Got {len(funding)} funding records")

        results = rolling_optimization(
            df, full_config, risk_config, dynamic_risk, symbol, funding,
            train_pct=0.4, step_pct=0.15, n_splits=4
        )

        if results:
            avg_pf = np.mean([r['pf'] for r in results])
            avg_wr = np.mean([r['wr'] for r in results])
            avg_ret = np.mean([r['ret'] for r in results])
            min_pf = min(r['pf'] for r in results)
            min_ret = min(r['ret'] for r in results)
            avg_dd = np.mean([r['dd'] for r in results])
            print(f"\n  SUMMARY:")
            print(f"    AVG: PF={avg_pf:.2f} WR={avg_wr:.0%} Ret={avg_ret:+.1%} DD={avg_dd:.1%}")
            print(f"    MIN: PF={min_pf:.2f} Ret={min_ret:+.1%} (worst OOS segment)")
            print(f"    Segments: {len(results)}")

            # Show worst segment details
            worst = min(results, key=lambda r: r['pf'])
            print(f"\n  WORST SEGMENT: Split {worst['split']}")
            print(f"    Test: {worst['test_period']}")
            print(f"    {worst['trades']}t PF={worst['pf']:.2f} WR={worst['wr']:.0%} Ret={worst['ret']:+.1%}")
            print(f"    Optimal params: {worst['opt_params']}")

        all_results[symbol] = results

    # Save report
    report_path = Path(__file__).parent / "backtest_results" / "rolling_optimization.txt"
    report_path.parent.mkdir(exist_ok=True)
    with open(report_path, 'w') as f:
        f.write(f"TRUE ROLLING WF OPTIMIZATION — {datetime.now()}\n")
        f.write(f"{'='*70}\n\n")
        for symbol, results in all_results.items():
            f.write(f"{symbol}\n")
            if results:
                for r in results:
                    f.write(f"  Split {r['split']}: {r['test_period']}\n")
                    f.write(f"    Train PF={r['opt_pf']:.2f} | "
                            f"OOS: {r['trades']}t PF={r['pf']:.2f} WR={r['wr']:.0%} "
                            f"Ret={r['ret']:+.1%} DD={r['dd']:.1%}\n")
                    f.write(f"    Params: {r['opt_params']}\n")
                    f.write(f"    Trailing: {r['opt_trailing']}\n")
                f.write(f"  AVG PF={np.mean([r['pf'] for r in results]):.2f} "
                        f"MIN PF={min(r['pf'] for r in results):.2f}\n")
            f.write("\n")

    print(f"\n  Report: {report_path}")


if __name__ == '__main__':
    main()
