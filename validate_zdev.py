"""Валидация ZDev (A 2.0 / R .20) — walk-forward по окнам ETH + другие монеты.
Не трогает бота. Usage: python validate_zdev.py
"""
import sys, time, yaml, ccxt, pandas as pd, numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import run_backtest, calculate_backtest_metrics
from strategies_v2 import generate_signals

CACHE_DIR = Path(__file__).parent / "data" / "raw"
ETH_CACHE = CACHE_DIR / "ETHUSDT_4h_3y.csv"
SPLIT_DATE = '2026-06-15'

RUNS = [
    ("ZDev A 2.0", "A", {'zdev_atr_min': 2.0}),
    ("ZDev R .20", "R", {'zdev_dev_min': 0.20}),
]


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None, retries=3):
    all_candles = []
    if since is None:
        since = exchange.parse8601('2023-06-01T00:00:00Z')
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


def load_eth():
    if ETH_CACHE.exists():
        return pd.read_csv(ETH_CACHE, parse_dates=['timestamp'])
    return pd.DataFrame()


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


def sig_bt(df, strat, variant, cfg_override, risk_config, dynamic_risk):
    signals = generate_signals(df, strat, variant=variant, cfg=cfg_override)
    if len(signals) < 3:
        return None
    trades, metrics = run_bt(df, signals, risk_config, dynamic_risk)
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
    avg_loss = abs(np.mean([t['pnl'] for t in losses])) if losses else 0
    rr = avg_win / avg_loss if avg_loss > 0 else 0
    return {**metrics, 'signals_raw': len(signals), 'avg_rr': rr}


def fmt(m):
    if m is None:
        return "  n/a"
    return (f"  sig={m['signals_raw']:>3} trd={m['total_trades']:>3} "
            f"WR={m['win_rate']:.0%} PF={m['profit_factor']:.2f} "
            f"Ret={m['total_return']:+.1%} DD={m['max_drawdown']:.1%} "
            f"AvgR={m['avg_rr']:.1f}")


def main():
    with open(Path(__file__).parent / "config" / "settings.yaml") as f:
        config = yaml.safe_load(f)
    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})
    out = []
    def log(s):
        print(s)
        out.append(s)

    eth = load_eth()
    if eth.empty:
        log("ETH cache missing — run backtest_v2_compare.py first")
        return

    log(f"=== WALK-FORWARD: ETH 4H, 6-month windows ===")
    start, end = eth['timestamp'].iloc[0], eth['timestamp'].iloc[-1]
    win_start = start
    wf = []
    while win_start < end:
        win_end = min(win_start + pd.Timedelta(days=183), end)
        df_w = eth[(eth['timestamp'] >= win_start) & (eth['timestamp'] < win_end)].reset_index(drop=True)
        if len(df_w) < 200:
            win_start = win_end
            continue
        log(f"\n  Window {df_w['timestamp'].iloc[0].date()} -> {df_w['timestamp'].iloc[-1].date()}  ({len(df_w)}c)")
        for label, variant, cfg in RUNS:
            m = sig_bt(df_w, 'zdev', variant, cfg, risk_config, dynamic_risk)
            log(f"    {label}:{fmt(m)}")
            if m is not None:
                wf.append({'win': str(win_start.date()), 'label': label, 'pf': m['profit_factor'], 'ret': m['total_return'], 'dd': m['max_drawdown'], 'n': m['total_trades']})
        win_start = win_end

    log(f"\n=== OUT-OF-SAMPLE REGIMES (ETH) ===")
    regimes = {
        '2026 (боковик)': ('2026-01-01', '2026-12-31'),
        '2023 (последние 4м)': ('2023-09-24', '2024-01-31'),
    }
    for name, (s, e) in regimes.items():
        df_r = eth[(eth['timestamp'] >= s) & (eth['timestamp'] < e)].reset_index(drop=True)
        if df_r.empty:
            continue
        log(f"\n  {name} ({len(df_r)}c)")
        for label, variant, cfg in RUNS:
            m = sig_bt(df_r, 'zdev', variant, cfg, risk_config, dynamic_risk)
            log(f"    {label}:{fmt(m)}")

    log(f"\n=== MULTI-COIN (4H, с 2023-06) ===")
    bybit = ccxt.bybit({'enableRateLimit': True})
    bybit.options['defaultType'] = 'linear'
    binance = ccxt.binance({'enableRateLimit': True})
    since_ms = bybit.parse8601('2023-06-01T00:00:00Z')

    coins = ['BNB/USDT:USDT', 'DOT/USDT:USDT', 'SOL/USDT:USDT', 'AVAX/USDT:USDT']
    for sym in coins:
        log(f"\n  {sym}")
        df = fetch_ohlcv(bybit, sym, since=since_ms)
        if df.empty:
            log("    no data")
            continue
        log(f"    {len(df)}c, {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()}")
        for label, variant, cfg in RUNS:
            m = sig_bt(df, 'zdev', variant, cfg, risk_config, dynamic_risk)
            log(f"      {label}:{fmt(m)}")

    log(f"\n  GRAM/USDT (TON+GRAM merge)")
    split_ts = pd.Timestamp(SPLIT_DATE)
    df_ton = fetch_ohlcv(binance, 'TON/USDT', since=since_ms)
    df_gram = fetch_ohlcv(bybit, 'GRAM/USDT:USDT', since=since_ms)
    df_ton_b = df_ton[df_ton['timestamp'] < split_ts].reset_index(drop=True) if not df_ton.empty else pd.DataFrame()
    df_gram_a = df_gram[df_gram['timestamp'] >= split_ts].reset_index(drop=True) if not df_gram.empty else pd.DataFrame()
    if not df_ton_b.empty and not df_gram_a.empty:
        dfg = pd.concat([df_ton_b, df_gram_a], ignore_index=True)
    elif not df_gram_a.empty:
        dfg = df_gram_a
    else:
        dfg = df_ton_b
    dfg = dfg.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    log(f"    {len(dfg)}c, {dfg['timestamp'].iloc[0].date() if not dfg.empty else '-'} -> {dfg['timestamp'].iloc[-1].date() if not dfg.empty else '-'}")
    if not dfg.empty:
        for label, variant, cfg in RUNS:
            m = sig_bt(dfg, 'zdev', variant, cfg, risk_config, dynamic_risk)
            log(f"      {label}:{fmt(m)}")

    if wf:
        log(f"\n=== WALK-FORWARD SUMMARY (PF по окнам) ===")
        for label in dict.fromkeys(w['label'] for w in wf):
            pfs = [w['pf'] for w in wf if w['label'] == label]
            wins = sum(1 for p in pfs if p >= 1.0)
            log(f"  {label}: окно-макс {max(pfs):.2f}, окно-мин {min(pfs):.2f}, "
                f"PF>=1 в {wins}/{len(pfs)} окон, средние {np.mean(pfs):.2f}")

    res = Path(__file__).parent / "backtest_results" / "zdev_validation.txt"
    res.write_text("\n".join(out), encoding='utf-8')
    log(f"\n  Saved to {res}")


if __name__ == '__main__':
    main()