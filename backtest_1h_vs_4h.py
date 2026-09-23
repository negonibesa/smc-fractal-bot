"""Сравнение ZDev A 2.0 на 4H vs 1H для одинаковых монет и периода.
Честность: одинаковые риск/комиссия/слиппедж из settings.yaml,
cooldown = 1 свеча таймфрейма (4.0h для 4H, 1.0h для 1H).
Данные: Bybit публичные klines, кэш в data/raw.
Usage: python backtest_1h_vs_4h.py
"""
import sys, time, yaml, ccxt, pandas as pd, numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import run_backtest, calculate_backtest_metrics
from strategies_v2 import generate_signals

SINCE = '2023-06-01T00:00:00Z'
COINS = [
    "ETH/USDT:USDT",
    "XRP/USDT:USDT",
    "DOGE/USDT:USDT",
    "SOL/USDT:USDT",
    "AVAX/USDT:USDT",
    "INJ/USDT:USDT",
    "SUI/USDT:USDT",
    "GRAM/USDT:USDT",
]

CACHE = Path(__file__).parent / "data" / "raw"
ZDEV = {'zdev_atr_min': 2.0}


def fetch_ohlcv(exchange, symbol, timeframe, since=SINCE, retries=3):
    cache_file = CACHE / f"{symbol.split('/')[0]}_{timeframe}_{SINCE[:10]}.csv"
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
            print("  Fetch failed, stopping pagination")
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


def run_bt(df, signals, risk_config, dynamic_risk, cooldown_hours):
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
        breakeven_at=0.3, trailing_activate=0.8, trailing_step=0.3,
        max_leverage=risk_config.get('max_leverage', 10),
        dynamic_risk=dynamic_risk,
        cooldown_hours=cooldown_hours,
        max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
        max_daily_trades=risk_config.get('max_daily_trades', 20),
        max_consecutive_losses=risk_config.get('max_consecutive_losses', 3),
    )
    return trades, metrics


def stats(trades, metrics):
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
    avg_loss = abs(np.mean([t['pnl'] for t in losses])) if losses else 0
    rr = avg_win / avg_loss if avg_loss > 0 else 0
    return {**metrics, 'avg_rr': rr}


def fmt(m):
    if m is None:
        return "      n/a"
    return (f" sig={m['signals_raw']:>3} trd={m['total_trades']:>3} WR={m['win_rate']:.0%} "
            f"PF={m['profit_factor']:.2f} Ret={m['total_return']:>+7.1%} DD={m['max_drawdown']:.1%}")


def main():
    with open(Path(__file__).parent / "config" / "settings.yaml", encoding='utf-8') as f:
        config = yaml.safe_load(f)
    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})

    bybit = ccxt.bybit({'enableRateLimit': True})
    bybit.options['defaultType'] = 'linear'

    rows = []
    for sym in COINS:
        print(f"\n{'='*78}\n  {sym}\n{'='*78}")
        df4 = fetch_ohlcv(bybit, sym, '4h')
        df1 = fetch_ohlcv(bybit, sym, '1h')
        if df4.empty or df1.empty:
            print("  no data")
            continue
        y4 = (df4['timestamp'].iloc[-1] - df4['timestamp'].iloc[0]).days / 365.25
        y1 = (df1['timestamp'].iloc[-1] - df1['timestamp'].iloc[0]).days / 365.25
        print(f"  4H: {len(df4)}c, {df4['timestamp'].iloc[0].date()} -> {df4['timestamp'].iloc[-1].date()} ({y4:.2f}Y)")
        print(f"  1H: {len(df1)}c, {df1['timestamp'].iloc[0].date()} -> {df1['timestamp'].iloc[-1].date()} ({y1:.2f}Y)")

        def run(df, cd):
            sig = generate_signals(df, 'zdev', variant='A', cfg=ZDEV)
            if len(sig) < 3:
                return None
            tr, mt = run_bt(df, sig, risk_config, dynamic_risk, cd)
            m = stats(tr, mt)
            m['signals_raw'] = len(sig)
            return m

        m4 = run(df4, 4.0)
        m1 = run(df1, 1.0)
        print(f"  4H:{fmt(m4)}")
        print(f"  1H:{fmt(m1)}")
        rows.append((sym.split('/')[0], y4, y1, m4, m1))

    print(f"\n{'='*110}")
    print("  ZDev A 2.0 — 4H vs 1H (одинаковый период с 2023-06)")
    print(f"{'='*110}")
    hdr = (f"  {'Coin':<6} {'Y4H':>4} {'Y1H':>4} | {'4H: trd':>6} {'WR':>4} {'PF':>5} {'Ret':>8} {'DD':>5} "
           f"| {'1H: trd':>6} {'WR':>4} {'PF':>5} {'Ret':>8} {'DD':>5}")
    print(hdr)
    print("  " + "-" * 104)
    for coin, y4, y1, m4, m1 in rows:
        s1 = (f" {m4['total_trades']:>3} {m4['win_rate']:.0%} {m4['profit_factor']:.2f} {m4['total_return']:>+7.1%} {m4['max_drawdown']:.1%}" if m4 else "      n/a")
        s2 = (f" {m1['total_trades']:>3} {m1['win_rate']:.0%} {m1['profit_factor']:.2f} {m1['total_return']:>+7.1%} {m1['max_drawdown']:.1%}" if m1 else "      n/a")
        print(f"  {coin:<6} {y4:>4.2f} {y1:>4.2f} |{s1} |{s2}")

    res = Path(__file__).parent / "backtest_results" / "zdev_4h_vs_1h.txt"
    res.write_text(
        "\n".join([hdr, "  " + "-" * 104] +
                  [f"  {c:<6} {y4:>4.2f} {y1:>4.2f} |" +
                   (f" {m4['total_trades']:>3} {m4['win_rate']:.0%} {m4['profit_factor']:.2f} {m4['total_return']:>+7.1%} {m4['max_drawdown']:.1%}" if m4 else "      n/a") +
                   " |" +
                   (f" {m1['total_trades']:>3} {m1['win_rate']:.0%} {m1['profit_factor']:.2f} {m1['total_return']:>+7.1%} {m1['max_drawdown']:.1%}" if m1 else "      n/a")
                   for c, y4, y1, m4, m1 in rows]),
        encoding='utf-8')
    print(f"\n  Saved to {res}")


if __name__ == '__main__':
    main()