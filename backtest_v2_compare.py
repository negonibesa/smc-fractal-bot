"""Сравнительный бектест стратегий v2 (бочонок A/B/C + equilibrium cross) на 3y ETH 4H.
Не трогает существующего бота. Считает 1-шт. входы, общий движок run_backtest.
Usage: python backtest_v2_compare.py
"""
import sys, time, yaml, ccxt, pandas as pd, numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import run_backtest, calculate_backtest_metrics
from strategies_v2 import generate_signals

YEARS = 3
CACHE = Path(__file__).parent / "data" / "raw" / "ETHUSDT_4h_3y.csv"


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None, retries=3):
    all_candles = []
    if since is None:
        since = exchange.parse8601('2022-01-01T00:00:00Z')
    while True:
        candles = None
        for attempt in range(retries):
            try:
                candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
                break
            except Exception as e:
                print(f"  Fetch error: {str(e)[:90]}, retrying in {7+attempt*6}s...")
                time.sleep(7 + attempt * 6)
        if not candles:
            print("  Fetch failed, stopping pagination")
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


def load_3y_data():
    if CACHE.exists():
        df = pd.read_csv(CACHE, parse_dates=['timestamp'])
        print(f"  Loaded cache: {len(df)} candles, {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
        return df
    print("  Fetching 3y ETH 4H from Bybit...")
    ex = ccxt.bybit({'enableRateLimit': True})
    ex.options['defaultType'] = 'linear'
    since_ms = ex.parse8601(f"{pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=365*YEARS):%Y-%m-%dT%H:%M:%SZ}")
    df = fetch_ohlcv(ex, 'ETH/USDT:USDT', since=since_ms)
    if df.empty:
        raise SystemExit("No data")
    df.to_csv(CACHE, index=False)
    print(f"  Cached to {CACHE}")
    return df


def run_bt(df, signals, risk_config, dynamic_risk):
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
        breakeven_at=0.3,
        trailing_activate=0.8,
        trailing_step=0.3,
        max_leverage=risk_config.get('max_leverage', 10),
        dynamic_risk=dynamic_risk,
        cooldown_hours=4.0,
        max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
        max_daily_trades=risk_config.get('max_daily_trades', 20),
        max_consecutive_losses=risk_config.get('max_consecutive_losses', 3),
    )
    return trades, metrics


def main():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})

    df = load_3y_data()
    years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
    print(f"  Period: {years:.1f} years")

    runs = [
        ("bochonok", "A (candles)", "A"),
        ("bochonok", "B (atr)", "B"),
        ("bochonok", "C (hybrid)", "C"),
        ("equilibrium_cross", "Cross", None),
        ("zdev", "ZDev R .10", "R", {'zdev_dev_min': 0.10}),
        ("zdev", "ZDev R .15", "R", {'zdev_dev_min': 0.15}),
        ("zdev", "ZDev R .20", "R", {'zdev_dev_min': 0.20}),
        ("zdev", "ZDev A 2.0", "A", {'zdev_atr_min': 2.0}),
        ("zdev", "ZDev A 3.0", "A", {'zdev_atr_min': 3.0}),
    ]

    results = {}
    for run in runs:
        strat, label, variant, *rest = run
        cfg_override = rest[0] if rest else None
        print(f"\n  Generating signals: {label} ...")
        signals = generate_signals(df, strat, variant=variant, cfg=cfg_override)
        print(f"  {len(signals)} signals")
        if len(signals) < 3:
            print("  Too few, skipping")
            continue
        trades, metrics = run_bt(df, signals, risk_config, dynamic_risk)
        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t['pnl'] for t in losses])) if losses else 0
        rr = avg_win / avg_loss if avg_loss > 0 else 0
        results[label] = {**metrics, 'signals_raw': len(signals), 'avg_rr': rr}

    print(f"\n{'='*78}")
    print(f"  STRATEGY COMPARISON — ETH 4H ({years:.1f}Y)")
    print(f"{'='*78}")
    hdr = f"  {'Strategy':<14} {'Sig':>4} {'Trd':>4} {'WR':>6} {'PF':>6} {'AvgR':>6} {'Ret':>8} {'DD':>7} {'Sharpe':>6} {'Final$':>9}"
    print(hdr)
    print("  " + "-" * 74)
    for label, m in results.items():
        print(f"  {label:<14} {m['signals_raw']:>4} {m['total_trades']:>4} "
              f"{m['win_rate']:.0%} {m['profit_factor']:>6.2f} {m['avg_rr']:>6.2f} "
              f"{m['total_return']:>+7.1%} {m['max_drawdown']:>6.1%} {m['sharpe_ratio']:>6.2f} "
              f"${m['final_balance']:>8,.0f}")

    summary_path = Path(__file__).parent / "backtest_results" / "v2_comparison.txt"
    summary_path.parent.mkdir(exist_ok=True)
    with open(summary_path, 'w') as f:
        f.write(f"STRATEGY V2 COMPARISON — ETH 4H, {years:.1f}Y\n{'='*60}\n")
        f.write(hdr.replace('  ', '') + "\n")
        f.write("-" * 74 + "\n")
        for label, m in results.items():
            f.write(f"  {label:<14} {m['signals_raw']:>4} {m['total_trades']:>4} "
                    f"{m['win_rate']:.0%} {m['profit_factor']:.2f} {m['avg_rr']:.2f} "
                    f"{m['total_return']:+.1%} {m['max_drawdown']:.1%} {m['sharpe_ratio']:.2f} "
                    f"${m['final_balance']:,.0f}\n")
    print(f"\n  Saved to {summary_path}")


if __name__ == '__main__':
    main()