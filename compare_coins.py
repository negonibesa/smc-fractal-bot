"""Сравнение текущей SMC vs ZDev A 2.0 по монетам за максимальный период (4H Bybit).
Usage: python compare_coins.py
"""
import sys, time, yaml, ccxt, pandas as pd, numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import run_backtest, calculate_backtest_metrics
from backtest_3y import run_segment
from strategies_v2 import generate_signals

COINS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "XRP/USDT:USDT",
    "DOGE/USDT:USDT",
    "SOL/USDT:USDT",
    "AVAX/USDT:USDT",
    "INJ/USDT:USDT",
    "SUI/USDT:USDT",
    "ONDO/USDT:USDT",
    "HYPE/USDT:USDT",
]

ZDEV = ("zdev", "A", {'zdev_atr_min': 2.0})


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None, retries=3):
    all_candles = []
    if since is None:
        since = exchange.parse8601('2020-08-01T00:00:00Z')
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
        breakeven_at=0.3, trailing_activate=0.8, trailing_step=0.3,
        max_leverage=risk_config.get('max_leverage', 10),
        dynamic_risk=dynamic_risk,
        cooldown_hours=4.0,
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
    return (f" sig={m['total_trades']:>3} WR={m['win_rate']:.0%} PF={m['profit_factor']:.2f} "
            f"Ret={m['total_return']:>+7.1%} DD={m['max_drawdown']:.1%}")


def main():
    with open(Path(__file__).parent / "config" / "settings.yaml") as f:
        config = yaml.safe_load(f)
    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})

    bybit = ccxt.bybit({'enableRateLimit': True})
    bybit.options['defaultType'] = 'linear'

    rows = []
    for sym in COINS:
        print(f"\n{'='*74}\n  {sym}\n{'='*74}")
        df = fetch_ohlcv(bybit, sym)
        if df.empty:
            print("  no data")
            continue
        years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
        print(f"  {len(df)}c, {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()} ({years:.2f}Y)")

        settings_symbol = sym.split('/')[0] + 'USDT'
        pair_config = get_pair_config(config, settings_symbol)

        print("  SMC (current)...")
        smc = run_segment(df, pair_config, config, risk_config, dynamic_risk, None, settings_symbol)
        smc_m = stats(*smc) if smc else None

        print("  ZDev A 2.0...")
        sig_z = generate_signals(df, ZDEV[0], variant=ZDEV[1], cfg=ZDEV[2])
        if len(sig_z) >= 3:
            tr, mt = run_bt(df, sig_z, risk_config, dynamic_risk)
            z_m = stats(tr, mt)
            z_m['signals_raw'] = len(sig_z)
        else:
            z_m = None
        print(f"      SMC :{fmt(smc_m)}")
        print(f"      ZDev:{fmt(z_m)}")
        rows.append((sym.split('/')[0], years, len(df), smc_m, z_m))

    print(f"\n{'='*110}")
    print("  ITOGOVAYA TABLITSA — SMC vs ZDev A 2.0 (max period, 4H)")
    print(f"{'='*110}")
    hdr = (f"  {'Coin':<6} {'Y':>4} {'Cdl':>5} | {'SMC: trd':>7} {'WR':>4} {'PF':>5} "
           f"{'Ret':>8} {'DD':>5} | {'ZDev: trd':>8} {'WR':>4} {'PF':>5} {'Ret':>8} {'DD':>5}")
    print(hdr)
    print("  " + "-" * 104)
    for coin, years, n, smc_m, z_m in rows:
        s1 = (f" {smc_m['total_trades']:>3} {smc_m['win_rate']:.0%} {smc_m['profit_factor']:.2f} {smc_m['total_return']:>+7.1%} {smc_m['max_drawdown']:.1%}" if smc_m else "      n/a")
        s2 = (f" {z_m['total_trades']:>3} {z_m['win_rate']:.0%} {z_m['profit_factor']:.2f} {z_m['total_return']:>+7.1%} {z_m['max_drawdown']:.1%}" if z_m else "      n/a")
        print(f"  {coin:<6} {years:>4.2f} {n:>5} |{s1} |{s2}")

    res = Path(__file__).parent / "backtest_results" / "coins_smc_vs_zdev.txt"
    res.write_text(
        "\n".join([hdr, "  " + "-" * 104] +
                  [f"  {c:<6} {y:>4.2f} {n:>5} |"
                   + (f" {smc['total_trades']:>3} {smc['win_rate']:.0%} {smc['profit_factor']:.2f} {smc['total_return']:>+7.1%} {smc['max_drawdown']:.1%}" if smc else "      n/a")
                   + " |"
                   + (f" {z['total_trades']:>3} {z['win_rate']:.0%} {z['profit_factor']:.2f} {z['total_return']:>+7.1%} {z['max_drawdown']:.1%}" if z else "      n/a")
                   for c, y, n, smc, z in rows]),
        encoding='utf-8')
    print(f"\n  Saved to {res}")


def get_pair_config(config, symbol):
    for asset in config.get('assets', []):
        if asset['symbol'] == symbol:
            return asset.get('config', {})
    return {}


if __name__ == '__main__':
    main()