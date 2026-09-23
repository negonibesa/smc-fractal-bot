"""Сводная статистика по бектесту: LONG vs SHORT, профит, макс. просадка за период.
ZDev A 2.0, 4H (использует кэш data/raw).
Usage: python backtest_summary.py [days=180]  (обрезает данные до последних N дней)
"""
import sys, yaml, ccxt, numpy as np, pandas as pd
from pathlib import Path
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).parent))
from backtest_1h_vs_4h import COINS, fetch_ohlcv, run_bt, stats
from strategies_v2 import generate_signals
from backtest import calculate_backtest_metrics

CACHE = Path(__file__).parent / "data" / "raw"
ZDEV = {'zdev_atr_min': 2.0, 'z_threshold': 2.0}
bybit = ccxt.bybit({'enableRateLimit': True})
bybit.options['defaultType'] = 'linear'


def side_stats(trades):
    if not trades:
        return {'n': 0, 'pnl': 0.0, 'wr': 0, 'pf': 0.0, 'avg': 0.0, 'best': 0.0, 'worst': 0.0}
    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p <= 0]
    return {
        'n': len(trades),
        'pnl': sum(pnls),
        'wr': len(wins) / len(trades),
        'pf': (sum(wins) / sum(losses)) if losses else (float('inf') if wins else 0.0),
        'avg': float(np.mean(pnls)),
        'best': float(np.max(pnls)) if pnls else 0.0,
        'worst': float(np.min(pnls)) if pnls else 0.0,
    }


def max_dd(trades, initial=10000):
    bal = initial
    peak = initial
    mdd = 0.0
    for t in trades:
        bal += t['pnl']
        peak = max(peak, bal)
        mdd = max(mdd, (peak - bal) / peak if peak > 0 else 0)
    return mdd


def main():
    days_ago = int(sys.argv[1]) if len(sys.argv) > 1 else None
    with open(Path(__file__).parent / "config" / "settings.yaml", encoding='utf-8') as f:
        config = yaml.safe_load(f)
    risk_config, dynamic_risk = config.get('risk', {}), config.get('dynamic_risk', {})

    total_all, total_long, total_short = [], [], []
    rows = []
    print(f"{'Coin':<7} | {'Все':>4} | {'L':>3} {'L PnL':>10} {'L WR':>5} {'L PF':>5} | {'S':>3} {'S PnL':>10} {'S WR':>5} {'S PF':>5} | {'DD':>6}")
    print("-" * 88)

    for sym in COINS:
        coin = sym.split('/')[0]
        df = fetch_ohlcv(bybit, sym, '4h')
        if df.empty:
            continue
        if days_ago is not None:
            cutoff = df['timestamp'].iloc[-1] - timedelta(days=days_ago)
            df = df[df['timestamp'] >= cutoff].reset_index(drop=True)
            period_days = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days + 1
        else:
            period_days = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days + 1
        sig = generate_signals(df, 'zdev', variant='A', cfg=ZDEV)
        if len(sig) < 3:
            print(f"{coin:<7} | too few signals ({len(sig)})")
            continue
        trades, _ = run_bt(df, sig, risk_config, dynamic_risk, 4.0)
        longs = [t for t in trades if t['direction'] == 'LONG']
        shorts = [t for t in trades if t['direction'] == 'SHORT']
        ls, ss = side_stats(longs), side_stats(shorts)
        dd = max_dd(trades)
        pnl_all = sum(t['pnl'] for t in trades)
        rows.append((coin, trades, ls, ss, dd, pnl_all))
        total_all += trades
        total_long += longs
        total_short += shorts

        print(f"{coin:<7} | {len(trades):>4} | {ls['n']:>3} {ls['pnl']:>+10.0f} {ls['wr']:>5.0%} {ls['pf']:>5.2f} "
              f"| {ss['n']:>3} {ss['pnl']:>+10.0f} {ss['wr']:>5.0%} {ss['pf']:>5.2f} | {dd:>5.1%}")

    print("-" * 88)
    tl, ts = side_stats(total_long), side_stats(total_short)
    print(f"TOTAL  | {len(total_all):>4} | {tl['n']:>3} {tl['pnl']:>+10.0f} {tl['wr']:>5.0%} {tl['pf']:>5.2f} "
          f"| {ts['n']:>3} {ts['pnl']:>+10.0f} {ts['wr']:>5.0%} {ts['pf']:>5.2f} | {max_dd(total_all):>5.1%}")

    # Портфельная просадка по общей последовательности сделок (по времени)
    all_sorted = sorted(total_all, key=lambda t: (t['entry_time'], t['exit_time']))
    print(f"\nПортфель из {len(COINS)} монет, сделок всего: {len(all_sorted)}")
    print(f"  Суммарный PnL: ${sum(t['pnl'] for t in all_sorted):,.0f}")
    print(f"  Макс. просадка по последовательности сделок (start $10k): {max_dd(all_sorted):.1%}")
    m = calculate_backtest_metrics(all_sorted, 10000)
    print(f"  Общий PF: {m['profit_factor']:.2f} | WR: {m['win_rate']:.0%} | Ret: {m['total_return']:+.1%}")

    # Сохранить
    os = []
    os.append(f"LONG: n={tl['n']} totalPnL=${tl['pnl']:,.0f} avg=${tl['avg']:,.0f} WR={tl['wr']:.0%} PF={tl['pf']:.2f} best=${tl['best']:,.0f} worst=${tl['worst']:,.0f}")
    os.append(f"SHORT: n={ts['n']} totalPnL=${ts['pnl']:,.0f} avg=${ts['avg']:,.0f} WR={ts['wr']:.0%} PF={ts['pf']:.2f} best=${ts['best']:,.0f} worst=${ts['worst']:,.0f}")
    os.append(f"MAX DD period: portfolio seq={max_dd(all_sorted):.1%}")
    os.append(f"Portfolio: trades={len(all_sorted)} PF={m['profit_factor']:.2f} WR={m['win_rate']:.0%} Ret={m['total_return']:+.1%} PnL=${sum(t['pnl'] for t in all_sorted):,.0f}")
    res = Path(__file__).parent / "backtest_results" / "zdev_summary_ls.txt"
    res.write_text("\n".join(os), encoding='utf-8')
    print(f"\nSaved to {res}")


if __name__ == '__main__':
    main()