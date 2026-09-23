"""Скрининг топ-20 Bybit linear perps на ZDev A 2.0 (4H).
1) Полный период: PF, Ret, DD.
2) Фильтр: PF > 1.5, DD < 10%.
3) Walk-forward (6 окон по ~183 дня): PF >= 1 в >= 4 окнах.
4) Из прошедших — выбор 3-5 максимально некоррелированных (по дневным лог-доходностям).

Usage: python screen_zdev.py
"""
import sys, time, yaml, ccxt, itertools, numpy as np, pandas as pd, requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import run_backtest
from strategies_v2 import generate_signals

CACHE = Path(__file__).parent / "data" / "raw"
ZDEV = {'zdev_atr_min': 2.0, 'z_threshold': 2.0}
SINCE = '2020-01-01T00:00:00Z'
WIN_DAYS = 183
MIN_WINDOWS = 4

STABLE = {'USDC', 'USDT', 'DAI', 'FDUSD', 'TUSD', 'PYUSD', 'EUR', 'BIT'}


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=SINCE, retries=3):
    cache_file = CACHE / f"{symbol.split('/')[0]}_{timeframe}_{since[:10]}_top20.csv"
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
                print(f"  Fetch error: {str(e)[:85]}, retrying in {7+attempt*6}s...")
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


def get_top_symbols(n=20):
    """Top-N линейных USDT перпетуалов Bybit по 24h обороту.
    Напрямую через public tickers (category=linear), без load_markets (гео-блок)."""
    r = requests.get('https://api.bybit.com/v5/market/tickers', params={'category': 'linear'}, timeout=30)
    r.raise_for_status()
    data = r.json().get('result', {}).get('list', [])
    rows = []
    seen = set()
    for t in data:
        sym = t.get('symbol', '')
        if not sym.endswith('USDT'):
            continue
        base = sym[:-4]
        if base in STABLE or base in seen:
            continue
        vol = float(t.get('turnover24h') or 0)
        if vol <= 0:
            continue
        seen.add(base)
        rows.append((f"{base}/USDT:USDT", base, vol))
    rows.sort(key=lambda r: r[2], reverse=True)
    return [r[0] for r in rows[:n]], [(r[0], round(r[2] / 1e9, 2)) for r in rows[:n]]


def run_backtests_exchange(df, risk_config, dynamic_risk):
    if df.empty or len(df) < 300:
        return None
    sig = generate_signals(df, 'zdev', variant='A', cfg=ZDEV)
    if len(sig) < 3:
        return {'signals': len(sig), 'trades': 0, 'pf': 0.0, 'ret': 0.0, 'dd': 0.0, 'wr': 0.0}
    bt_df = df.copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in sig}
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
    wins = [t for t in trades if t['pnl'] > 0]
    return {
        'signals': len(sig),
        'trades': len(trades),
        'pf': metrics['profit_factor'],
        'ret': metrics['total_return'],
        'dd': metrics['max_drawdown'],
        'wr': metrics['win_rate'],
        'n_win': len(wins),
        'n_loss': len(trades) - len(wins),
        'trades_obj': trades,
    }


def walk_forward(df, risk_config, dynamic_risk):
    if df.empty:
        return []
    start, end = df['timestamp'].iloc[0], df['timestamp'].iloc[-1]
    win_start = start
    wf = []
    while win_start < end:
        win_end = min(win_start + pd.Timedelta(days=WIN_DAYS), end)
        df_w = df[(df['timestamp'] >= win_start) & (df['timestamp'] < win_end)].reset_index(drop=True)
        if len(df_w) < 200:
            win_start = win_end
            continue
        r = run_backtests_exchange(df_w, risk_config, dynamic_risk)
        wf.append({'start': str(win_start.date()), 'pf': r['pf'] if r else 0.0,
                   'trades': r['trades'] if r else 0})
        win_start = win_end
        if len(wf) >= 6:
            break
    return wf


def daily_returns(df):
    d = df.copy()
    d['date'] = d['timestamp'].dt.date
    daily = d.groupby('date')['close'].last()
    return np.log(daily).diff().dropna()


def greedy_uncorrelated(coins, max_pick=5, max_corr=0.7):
    """Greedy: берём самую прибыльную, затем добавляем кандидата с макс. мин. дистанцией
    по корреляции от уже выбранных. Возвращает порядок выбора."""
    picked = []
    remaining = list(coins)
    while len(picked) < max_pick and remaining:
        if not picked:
            best = max(remaining, key=lambda c: c['pf'])
        else:
            best = None
            best_score = -1e9
            for c in remaining:
                corrs = [c['corr'][p['coin']] for p in picked if p['coin'] in c['corr']]
                if not corrs:
                    continue
                min_dist = min(1.0 - abs(x) if x is not None else 1.0 for x in corrs)
                if min_dist > best_score:
                    best_score = min_dist
                    best = c
            if best is None:
                best = max(remaining, key=lambda c: c['pf'])
        picked.append(best)
        remaining.remove(best)
    return picked


def main():
    with open(Path(__file__).parent / "config" / "settings.yaml", encoding='utf-8') as f:
        config = yaml.safe_load(f)
    risk_config, dynamic_risk = config.get('risk', {}), config.get('dynamic_risk', {})

    bybit = ccxt.bybit({'enableRateLimit': True})
    bybit.options['defaultType'] = 'linear'

    symbols, vol = get_top_symbols(20)
    out = [f"TOP-20 Bybit linear perps (24h quoteVol $B): {vol}"]
    print("\n  TOP-20 (24h объём, $B):")
    for s, v in vol:
        print(f"    {s:<22} {v}")

    results = {}  # base -> results
    for sym in symbols:
        base = sym.split('/')[0]
        print(f"\n  === {sym} ===", flush=True)
        df = fetch_ohlcv(bybit, sym, '4h')
        if df.empty:
            out.append(f"{base}: NO DATA")
            continue
        yrs = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
        r = run_backtests_exchange(df, risk_config, dynamic_risk)
        if r is None:
            out.append(f"{base}: SKIP (len<300)")
            continue
        wf = walk_forward(df, risk_config, dynamic_risk)
        wf_ok = sum(1 for w in wf if w['pf'] >= 1.0)
        passes = (r['pf'] > 1.5 and r['dd'] < 0.10 and wf_ok >= 4)
        results[base] = {'coin': base, 'yrs': yrs, **r, 'wf': wf, 'wf_ok': wf_ok,
                         'n_wf': len(wf), 'pass': passes}
        print(f"    {yrs:.2f}Y trd={r['trades']} PF={r['pf']:.2f} Ret={r['ret']:+.1%} DD={r['dd']:.1%} "
              f"WR={r['wr']:.0%} | WF PF>=1: {wf_ok}/{len(wf)} {'PASS' if passes else ''}")
        for w in wf:
            print(f"      win {w['start']}: PF={w['pf']:.2f} trd={w['trades']}")
        out.append(f"{base} | {yrs:.2f}Y | trd={r['trades']} PF={r['pf']:.2f} Ret={r['ret']:+.1%} "
                   f"DD={r['dd']:.1%} WR={r['wr']:.0%} | WF {wf_ok}/{len(wf)} | {'PASS' if passes else 'fail'}")

    passed = [v for v in results.values() if v['pass']]
    print(f"\n  PASSED filter (PF>1.5, DD<10%, WF>=4/6): {[v['coin'] for v in passed]}")
    out.append(f"\nPASSED: {[v['coin'] for v in passed]}")

    if not passed:
        out.append("No coins passed the filter.")
        res = Path(__file__).parent / "backtest_results" / "screen_zdev_top20.txt"
        res.write_text("\n".join(out), encoding='utf-8')
        print(f"\n  Saved to {res}")
        return

    # Correlation matrix on daily returns over overlapping period
    daily = {}
    for sym in symbols:
        base = sym.split('/')[0]
        df = fetch_ohlcv(bybit, sym, '4h')
        daily[base] = daily_returns(df)
    dfr = pd.DataFrame(daily)
    dfr = dfr.dropna(how='any')
    corr = dfr.corr()

    print("\n  Correlation matrix (daily log returns, overlapping period):")
    corr_s = corr.round(2)
    print("  " + "  ".join(f"{c:<7}" for c in corr_s.columns[corr_s.columns.isin([v['coin'] for v in passed])]))
    for i, c1 in enumerate(passed):
        row = " ".join(f"{corr_s.loc[c1['coin'], c2['coin']]:<7.2f}" for c2 in passed)
        print(f"  {c1['coin']:<7} {row}")

    # attach corr dict
    for v in passed:
        v['corr'] = {c: float(corr.loc[v['coin'], c]) if c in corr.index else None for c in corr.index}

    # greedy pick 3-5
    for k in (3, 4, 5):
        picked = greedy_uncorrelated(passed, max_pick=k)
        avg_corr = np.mean([abs(p['corr'][q['coin']]) for p in picked for q in picked if p['coin'] != q['coin'] and q['coin'] in p['corr']])
        names = [p['coin'] for p in picked]
        print(f"\n  Пул из {k}: {names}  (avg |corr|={avg_corr:.2f})")
        out.append(f"PICK {k}: {names}  avg|corr|={avg_corr:.2f}")

    res = Path(__file__).parent / "backtest_results" / "screen_zdev_top20.txt"
    res.write_text("\n".join(out), encoding='utf-8')
    print(f"\n  Saved to {res}")


if __name__ == '__main__':
    main()