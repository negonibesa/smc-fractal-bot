"""
Walk-Forward validation — train/test split, out-of-sample metrics.
Addresses audit concern: are results in-sample or walk-forward?
"""
import sys
import time
import yaml
import ccxt
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from backtest import run_backtest, calculate_backtest_metrics
from main import SignalGenerator


def ccxt_symbol(bybit_symbol: str) -> str:
    base = bybit_symbol.replace('USDT', '')
    return f"{base}/USDT:USDT"


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


def fetch_funding_rates(exchange, symbol, since=None):
    """Fetch full funding rate history from Bybit via backward pagination.

    Forward 'since' pagination is broken on Bybit (ignores since, returns same 200).
    Backward pagination via endTime works: walk from most recent to oldest.
    """
    all_rates = []
    page = 0
    end_time = None

    while True:
        page += 1
        kwargs = {'limit': 200}
        if end_time is not None:
            kwargs['endTime'] = end_time
        try:
            rates = exchange.fetch_funding_rate_history(symbol, **kwargs)
        except Exception as e:
            print(f"  Funding fetch error: {e}, retrying...")
            time.sleep(3)
            try:
                rates = exchange.fetch_funding_rate_history(symbol, **kwargs)
            except:
                break
        if not rates:
            break
        all_rates.extend(rates)
        print(f"  Funding page {page}: {len(rates)} records, "
              f"{pd.Timestamp(rates[0]['timestamp'], unit='ms')} to "
              f"{pd.Timestamp(rates[-1]['timestamp'], unit='ms')}, "
              f"total: {len(all_rates)}")
        end_time = rates[-1]['timestamp'] - 1
        if len(rates) < 200:
            print(f"  Funding: last page ({len(rates)} < 200), stopping")
            break
        time.sleep(0.2)

    if not all_rates:
        return pd.DataFrame(columns=['timestamp', 'rate'])
    df = pd.DataFrame([{
        'timestamp': pd.Timestamp(r['timestamp'], unit='ms'),
        'rate': r['fundingRate']
    } for r in all_rates])
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df


def run_bt(df, signals, config, risk_config, dynamic_risk, funding_rates=None):
    """Run backtest on a segment."""
    bt_df = df.copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    signals_dict = {s['timestamp']: s for s in signals}
    bt_signals = [signals_dict[ts] for ts in bt_df.index if ts in signals_dict]
    if not bt_signals:
        return None, []
    trailing = config.get('trailing', {})
    trades, metrics = run_backtest(
        bt_df, bt_signals,
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


def generate_signals(df, sig_config, symbol):
    """Generate signals for a full dataframe."""
    gen = SignalGenerator(sig_config, symbol=symbol)
    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            sig['timestamp'] = str(df['timestamp'].iloc[i])
            signals.append(sig)
    return signals


def anchored_walk_forward(df, config, risk_config, dynamic_risk, symbol,
                          n_splits=5, min_train_pct=0.4, funding_rates=None):
    """
    Anchored walk-forward: train on [0..split_i], test on [split_i..split_i+1].
    Train window grows, test window is fixed.
    """
    n = len(df)
    test_size = n // (n_splits + 1)
    train_min = int(n * min_train_pct)

    results = []

    for i in range(n_splits):
        train_end = train_min + i * test_size
        test_start = train_end
        test_end = min(test_start + test_size, n)

        if test_end <= test_start or test_end - test_start < 20:
            continue

        train_df = df.iloc[:train_end].copy().reset_index(drop=True)
        test_df = df.iloc[test_start:test_end].copy().reset_index(drop=True)

        sig_config = {
            'strategy': config.get('strategy', {}),
            'filters': config.get('filters', {}),
        }

        # Generate signals separately for train and test
        train_signals = generate_signals(train_df, sig_config, symbol)
        test_signals = generate_signals(test_df, sig_config, symbol)

        # Run backtest on OOS test data
        if len(test_signals) < 3:
            continue

        metrics, trades = run_bt(test_df, test_signals, config, risk_config, dynamic_risk,
                                  funding_rates=funding_rates)
        if metrics is None:
            continue

        train_start = str(train_df['timestamp'].iloc[0])[:10]
        train_end_date = str(train_df['timestamp'].iloc[-1])[:10]
        test_start_date = str(test_df['timestamp'].iloc[0])[:10]
        test_end_date = str(test_df['timestamp'].iloc[-1])[:10]

        results.append({
            'split': i + 1,
            'train_period': f"{train_start} to {train_end_date}",
            'test_period': f"{test_start_date} to {test_end_date}",
            'train_candles': len(train_df),
            'test_candles': len(test_df),
            'trades': metrics['total_trades'],
            'pf': metrics['profit_factor'],
            'wr': metrics['win_rate'],
            'ret': metrics['total_return'],
            'dd': metrics['max_drawdown'],
        })

    return results


def rolling_walk_forward(df, config, risk_config, dynamic_risk, symbol,
                         window_pct=0.4, step_pct=0.15, n_splits=5, funding_rates=None):
    """
    Rolling walk-forward: fixed-size train window, slides forward.
    """
    n = len(df)
    window_size = int(n * window_pct)
    step_size = int(n * step_pct)

    results = []

    for i in range(n_splits):
        start = i * step_size
        train_end = start + window_size
        test_start = train_end
        test_end = min(test_start + window_size, n)

        if test_end <= test_start or test_end - test_start < 20 or train_end > n:
            continue

        train_df = df.iloc[start:train_end].copy().reset_index(drop=True)
        test_df = df.iloc[test_start:test_end].copy().reset_index(drop=True)

        sig_config = {
            'strategy': config.get('strategy', {}),
            'filters': config.get('filters', {}),
        }

        test_signals = generate_signals(test_df, sig_config, symbol)

        if len(test_signals) < 3:
            continue

        metrics, trades = run_bt(test_df, test_signals, config, risk_config, dynamic_risk,
                                  funding_rates=funding_rates)
        if metrics is None:
            continue

        test_start_date = str(test_df['timestamp'].iloc[0])[:10]
        test_end_date = str(test_df['timestamp'].iloc[-1])[:10]

        results.append({
            'split': i + 1,
            'test_period': f"{test_start_date} to {test_end_date}",
            'train_candles': len(train_df),
            'test_candles': len(test_df),
            'trades': metrics['total_trades'],
            'pf': metrics['profit_factor'],
            'wr': metrics['win_rate'],
            'ret': metrics['total_return'],
            'dd': metrics['max_drawdown'],
        })

    return results


def main():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})

    exchange = ccxt.bybit({'enableRateLimit': True})

    assets = [a for a in config.get('assets', []) if a.get('enabled', True)]

    all_results = {}

    for asset in assets:
        symbol = asset['symbol']
        asset_config = asset.get('config', config)
        full_config = {
            'strategy': {**config.get('strategy', {}), **asset_config.get('strategy', {})},
            'filters': {**config.get('filters', {}), **asset_config.get('filters', {})},
            'trailing': {**config.get('trailing', {}), **asset_config.get('trailing', {})},
        }

        print(f"\n{'='*70}")
        print(f"  {symbol} — Walk-Forward Validation")
        print(f"{'='*70}")

        df = fetch_ohlcv(exchange, ccxt_symbol(symbol), timeframe='4h')
        if df.empty:
            print(f"  No data, skipping")
            continue

        years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
        print(f"  {len(df)} candles, {years:.1f}Y")

        print(f"  Fetching funding rates...")
        funding_rates = fetch_funding_rates(exchange, ccxt_symbol(symbol))
        print(f"  Got {len(funding_rates)} funding rate records")

        # ─── Anchored walk-forward ────────────────────────────────
        print(f"\n  ANCHORED WALK-FORWARD (train grows, test fixed):")
        anchored = anchored_walk_forward(df, full_config, risk_config, dynamic_risk, symbol,
                                          n_splits=5, funding_rates=funding_rates if not funding_rates.empty else None)

        if anchored:
            for r in anchored:
                flag = "+" if r['pf'] >= 1.2 else ("~" if r['pf'] >= 1.0 else "-")
                print(f"    Split {r['split']}: {r['test_period']} | "
                      f"{r['trades']:3d}t PF={r['pf']:5.2f} WR={r['wr']:.0%} "
                      f"Ret={r['ret']:+.1%} DD={r['dd']:.1%} {flag}")

            avg_pf = np.mean([r['pf'] for r in anchored])
            avg_wr = np.mean([r['wr'] for r in anchored])
            avg_ret = np.mean([r['ret'] for r in anchored])
            min_pf = min(r['pf'] for r in anchored)
            print(f"    -----------------------------------------")
            print(f"    AVG:  PF={avg_pf:.2f}  WR={avg_wr:.0%}  Ret={avg_ret:+.1%}")
            print(f"    MIN:  PF={min_pf:.2f}  (worst split)")
        else:
            print(f"    Insufficient data for walk-forward")

        # Rolling walk-forward
        print(f"\n  ROLLING WALK-FORWARD (fixed window, slides forward):")
        rolling = rolling_walk_forward(df, full_config, risk_config, dynamic_risk, symbol,
                                        n_splits=4, funding_rates=funding_rates if not funding_rates.empty else None)

        if rolling:
            for r in rolling:
                flag = "+" if r['pf'] >= 1.2 else ("~" if r['pf'] >= 1.0 else "-")
                print(f"    Split {r['split']}: {r['test_period']} | "
                      f"{r['trades']:3d}t PF={r['pf']:5.2f} WR={r['wr']:.0%} "
                      f"Ret={r['ret']:+.1%} DD={r['dd']:.1%} {flag}")

            avg_pf = np.mean([r['pf'] for r in rolling])
            avg_wr = np.mean([r['wr'] for r in rolling])
            avg_ret = np.mean([r['ret'] for r in rolling])
            min_pf = min(r['pf'] for r in rolling)
            print(f"    -----------------------------------------")
            print(f"    AVG:  PF={avg_pf:.2f}  WR={avg_wr:.0%}  Ret={avg_ret:+.1%}")
            print(f"    MIN:  PF={min_pf:.2f}  (worst split)")

        all_results[symbol] = {
            'years': years,
            'anchored': anchored,
            'rolling': rolling,
        }

    # ─── SUMMARY ──────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  WALK-FORWARD SUMMARY")
    print(f"{'='*70}")
    print(f"  {'Symbol':12s} {'Anchored PF':>12s} {'Min PF':>8s} {'Rolling PF':>12s} {'Min PF':>8s}  Verdict")
    print(f"  {'-'*70}")

    for symbol, data in all_results.items():
        a = data['anchored']
        r = data['rolling']

        a_pf = f"{np.mean([x['pf'] for x in a]):.2f}" if a else "N/A"
        a_min = f"{min(x['pf'] for x in a):.2f}" if a else "N/A"
        r_pf = f"{np.mean([x['pf'] for x in r]):.2f}" if r else "N/A"
        r_min = f"{min(x['pf'] for x in r):.2f}" if r else "N/A"

        # Verdict
        if a and r:
            avg_min = min(float(a_min), float(r_min))
            avg_all = (float(a_pf) + float(r_pf)) / 2
            if avg_min >= 1.2 and avg_all >= 1.3:
                verdict = "KEEP"
            elif avg_min >= 1.0:
                verdict = "MARGINAL"
            else:
                verdict = "REMOVE"
        else:
            verdict = "INSUFFICIENT"

        print(f"  {symbol:12s} {a_pf:>12s} {a_min:>8s} {r_pf:>12s} {r_min:>8s}  {verdict}")

    # Save
    report_path = Path(__file__).parent / "backtest_results" / "walk_forward.txt"
    report_path.parent.mkdir(exist_ok=True)
    with open(report_path, 'w') as f:
        f.write(f"WALK-FORWARD VALIDATION — {datetime.now()}\n")
        f.write(f"{'='*70}\n\n")
        for symbol, data in all_results.items():
            f.write(f"{symbol} ({data['years']:.1f}Y data)\n")
            f.write(f"  ANCHORED:\n")
            for r in data.get('anchored', []):
                f.write(f"    Split {r['split']}: {r['test_period']} | "
                        f"{r['trades']}t PF={r['pf']:.2f} WR={r['wr']:.0%} "
                        f"Ret={r['ret']:+.1%} DD={r['dd']:.1%}\n")
            f.write(f"  ROLLING:\n")
            for r in data.get('rolling', []):
                f.write(f"    Split {r['split']}: {r['test_period']} | "
                        f"{r['trades']}t PF={r['pf']:.2f} WR={r['wr']:.0%} "
                        f"Ret={r['ret']:+.1%} DD={r['dd']:.1%}\n")
            f.write("\n")

    print(f"\n  Report: {report_path}")


if __name__ == '__main__':
    main()
