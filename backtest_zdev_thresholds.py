"""Сравнение ZDev A на 4H: z_threshold 2.0 (боевой) vs 1.5.
Одинаковый период с 2023-06, те же риск/комиссии, cooldown = 1 свеча (4H).
Usage: python backtest_zdev_thresholds.py
"""
import sys, yaml, numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest_1h_vs_4h import COINS, fetch_ohlcv, run_bt, stats, fmt
from strategies_v2 import generate_signals

BASE = {'zdev_atr_min': 2.0}
CONFIGS = {
    'Z20 (боевой)': {**BASE, 'z_threshold': 2.0},
    'Z15':          {**BASE, 'z_threshold': 1.5},
}


def load():
    with open(Path(__file__).parent / "config" / "settings.yaml", encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config.get('risk', {}), config.get('dynamic_risk', {})


def main():
    risk_config, dynamic_risk = load()
    import ccxt
    bybit = ccxt.bybit({'enableRateLimit': True})
    bybit.options['defaultType'] = 'linear'

    rows = []
    for sym in COINS:
        print(f"\n{'='*78}\n  {sym}\n{'='*78}")
        df = fetch_ohlcv(bybit, sym, '4h')
        if df.empty:
            continue
        yrs = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
        print(f"  4H: {len(df)}c, {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()} ({yrs:.2f}Y)")

        results = {}
        for name, cfg in CONFIGS.items():
            sig = generate_signals(df, 'zdev', variant='A', cfg=cfg)
            if len(sig) < 3:
                print(f"  {name}: too few signals ({len(sig)})")
                results[name] = None
                continue
            tr, mt = run_bt(df, sig, risk_config, dynamic_risk, 4.0)
            m = stats(tr, mt)
            m['signals_raw'] = len(sig)
            results[name] = m
            print(f"  {name}:{fmt(m)}")
        rows.append((sym.split('/')[0], yrs, results))

    print(f"\n{'='*110}")
    print("  ZDev A 2.0xATR на 4H — z_threshold 2.0 vs 1.5 (период с 2023-06)")
    print(f"{'='*110}")
    hdr = (f"  {'Coin':<6} {'Y':>4} | {'Z20 trd':>6} {'WR':>4} {'PF':>5} {'Ret':>8} {'DD':>5} "
           f"| {'Z15 trd':>6} {'WR':>4} {'PF':>5} {'Ret':>8} {'DD':>5}")
    print(hdr)
    print("  " + "-" * 104)

    def cell(m):
        return (f" {m['total_trades']:>3} {m['win_rate']:.0%} {m['profit_factor']:.2f} {m['total_return']:>+7.1%} {m['max_drawdown']:.1%}" if m else "      n/a")

    out_lines = [hdr, "  " + "-" * 104]
    for coin, yrs, r in rows:
        line = f"  {coin:<6} {yrs:>4.2f} |{cell(r['Z20 (боевой)'])} |{cell(r['Z15'])}"
        print(line)
        out_lines.append(line)

    res = Path(__file__).parent / "backtest_results" / "zdev_threshold_20_vs_15.txt"
    res.write_text("\n".join(out_lines), encoding='utf-8')
    print(f"\n  Saved to {res}")


if __name__ == '__main__':
    main()