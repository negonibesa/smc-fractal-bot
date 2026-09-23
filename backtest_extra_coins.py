"""Бектест ZDev A 2.0 на дополнительных монетах за максимальную историю.
Монеты: ATOM, ADA, ARB, RENDER, NEAR. 4H от дебюта Bybit linear.
Usage: python backtest_extra_coins.py
"""
import sys, yaml, ccxt, time, numpy as np, pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import run_backtest, calculate_backtest_metrics
from backtest_1h_vs_4h import run_bt
from strategies_v2 import generate_signals

SINCE = '2020-01-01T00:00:00Z'
EXTRA = [
    "ATOM/USDT:USDT",
    "ADA/USDT:USDT",
    "ARB/USDT:USDT",
    "RENDER/USDT:USDT",
    "NEAR/USDT:USDT",
]
CACHE = Path(__file__).parent / "data" / "raw"
ZDEV = {'zdev_atr_min': 2.0, 'z_threshold': 2.0}


def fetch_ohlcv(exchange, symbol, timeframe, since=SINCE, retries=3):
    cache_file = CACHE / f"{symbol.split('/')[0]}_{timeframe}_{since[:10]}_max.csv"
    if cache_file.exists():
        return pd.read_csv(cache_file, parse_dates=['timestamp'])
    all_candles = []
    since_ms = exchange.parse8601(since)
    while True:
        candles = None
        for attempt in range(retries):
            try:
                candles = exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=1000)
                break
            except Exception as e:
                print(f"  Fetch error: {str(e)[:90]}, retrying in {7+attempt*6}s...")
                time.sleep(7 + attempt * 6)
        if not candles:
            break
        all_candles.extend(candles)
        since_ms = candles[-1][0] + 1
        if len(candles) < 1000:
            break
        time.sleep(0.2)
    if not all_candles:
        return pd.DataFrame()
    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    cache_file.parent.mkdir(exist_ok=True)
    df.to_csv(cache_file, index=False)
    return df


def side_stats(trades):
    if not trades:
        return {'n': 0, 'pnl': 0.0, 'wr': 0, 'pf': 0.0}
    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p <= 0]
    return {
        'n': len(trades),
        'pnl': sum(pnls),
        'wr': len(wins) / len(trades),
        'pf': (sum(wins) / sum(losses)) if losses else (float('inf') if wins else 0.0),
    }


def max_dd(trades, initial=10000):
    bal, peak, mdd = initial, initial, 0.0
    for t in trades:
        bal += t['pnl']
        peak = max(peak, bal)
        mdd = max(mdd, (peak - bal) / peak if peak > 0 else 0)
    return mdd


def main():
    with open(Path(__file__).parent / "config" / "settings.yaml", encoding='utf-8') as f:
        config = yaml.safe_load(f)
    risk_config, dynamic_risk = config.get('risk', {}), config.get('dynamic_risk', {})

    bybit = ccxt.bybit({'enableRateLimit': True})
    bybit.options['defaultType'] = 'linear'

    total_all = []
    rows = []
    print(f"{'Coin':<7} | {'Y':>4} | {'Все':>4} | {'L':>3} {'L PnL':>10} {'L WR':>5} {'L PF':>5} | {'S':>3} {'S PnL':>10} {'S WR':>5} {'S PF':>5} | {'DD':>6}")
    print("-" * 96)

    for sym in EXTRA:
        coin = sym.split('/')[0]
        print(f"\n  {sym}...", flush=True)
        df = fetch_ohlcv(bybit, sym, '4h')
        if df.empty:
            print(f"{coin:<7} | no data on Bybit")
            continue
        yrs = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
        sig = generate_signals(df, 'zdev', variant='A', cfg=ZDEV)
        if len(sig) < 3:
            print(f"{coin:<7} | {yrs:>4.2f} | too few signals ({len(sig)})")
            continue
        trades, _ = run_bt(df, sig, risk_config, dynamic_risk, 4.0)
        longs = [t for t in trades if t['direction'] == 'LONG']
        shorts = [t for t in trades if t['direction'] == 'SHORT']
        ls, ss = side_stats(longs), side_stats(shorts)
        dd = max_dd(trades)
        total_all += trades
        rows.append((coin, yrs, trades, ls, ss, dd))
        print(f"{coin:<7} | {yrs:>4.2f} | {len(trades):>4} | {ls['n']:>3} {ls['pnl']:>+10.0f} {ls['wr']:>5.0%} {ls['pf']:>5.2f} "
              f"| {ss['n']:>3} {ss['pnl']:>+10.0f} {ss['wr']:>5.0%} {ss['pf']:>5.2f} | {dd:>5.1%}")

    print("-" * 96)
    if not total_all:
        return
    tl, ts = side_stats([t for t in total_all if t['direction'] == 'LONG']), side_stats([t for t in total_all if t['direction'] == 'SHORT'])
    print(f"TOTAL  |       | {len(total_all):>4} | {tl['n']:>3} {tl['pnl']:>+10.0f} {tl['wr']:>5.0%} {tl['pf']:>5.2f} "
          f"| {ts['n']:>3} {ts['pnl']:>+10.0f} {ts['wr']:>5.0%} {ts['pf']:>5.2f} | {max_dd(total_all):>5.1%}")

    all_sorted = sorted(total_all, key=lambda t: (str(t['entry_time']), str(t['exit_time'])))
    m = calculate_backtest_metrics(all_sorted, 10000)
    print(f"\nПортфель 5 монет: сделок={len(all_sorted)} PF={m['profit_factor']:.2f} WR={m['win_rate']:.0%} "
          f"Ret={m['total_return']:+.1%} PnL=${sum(t['pnl'] for t in all_sorted):,.0f} DD(seq)={max_dd(all_sorted):.1%}")

    out = ["LONG: n={} PnL=${:,.0f} WR={:.0%} PF={:.2f}".format(tl['n'], tl['pnl'], tl['wr'], tl['pf']),
           "SHORT: n={} PnL=${:,.0f} WR={:.0%} PF={:.2f}".format(ts['n'], ts['pnl'], ts['wr'], ts['pf']),
           "MAX DD period (no coin-by-coin): {}".format("; ".join(f"{c} {dd:.1%}" for c, y, t, l, s, dd in rows)),
           "Portfolio: trades={} PF={:.2f} WR={:.0%} Ret={:+.1%} PnL=${:,.0f} DD(seq)={:.1%}".format(
               len(all_sorted), m['profit_factor'], m['win_rate'], m['total_return'],
               sum(t['pnl'] for t in all_sorted), max_dd(all_sorted))]
    res = Path(__file__).parent / "backtest_results" / "zdev_extra_coins.txt"
    res.write_text("\n".join(out), encoding='utf-8')
    print(f"\nSaved to {res}")


if __name__ == '__main__':
    main()