"""
Walk-forward analysis: check if BNB config is overfit.
Splits data into periods and tests each independently.
"""
import warnings; warnings.filterwarnings('ignore')
import yaml, numpy as np, pandas as pd
from pathlib import Path
from data_loader import BybitLoader
from backtest import run_backtest
from smc_features import find_consolidation_center, detect_sweep, calculate_adx

with open('config/best.yaml') as f:
    config = yaml.safe_load(f)
params = config['strategy']
filters = config['filters']
trailing = config['trailing']

df = pd.read_csv('data/raw/BNBUSDT_4h_12m.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'])
for c in ['open','high','low','close','volume']:
    df[c] = pd.to_numeric(df[c])
df = df.sort_values('timestamp').reset_index(drop=True)


def run_bt(df_part, label):
    center = find_consolidation_center(df_part, lookback=params['lookback'])
    sweep = detect_sweep(df_part, center, threshold=params['sweep_threshold'])

    adx, pdi, mdi = None, None, None
    if filters.get('adx_filter', {}).get('enabled'):
        adx, pdi, mdi = calculate_adx(df_part, period=14)

    signals = []
    state = 0; sw_dir = None; sw_price = None; sw_idx = None; ctr = None

    for i in range(params['lookback'], len(df_part)):
        c = center.iloc[i] if not pd.isna(center.iloc[i]) else df_part['close'].iloc[i]
        h = df_part['high'].iloc[i]
        l = df_part['low'].iloc[i]
        cl = df_part['close'].iloc[i]

        has_bull = sweep['bullish_sweep'].iloc[i] if i < len(sweep['bullish_sweep']) else False
        has_bear = sweep['bearish_sweep'].iloc[i] if i < len(sweep['bearish_sweep']) else False

        if state == 0:
            if has_bear:
                state=1; sw_dir='bearish'; sw_price=h; sw_idx=i; ctr=c
            elif has_bull:
                state=1; sw_dir='bullish'; sw_price=l; sw_idx=i; ctr=c
        elif state == 1:
            if abs(cl - ctr) / ctr < params['center_proximity']:
                skip = False
                if adx is not None:
                    cur_adx = adx.iloc[i] if not pd.isna(adx.iloc[i]) else 0
                    if cur_adx < filters['adx_filter'].get('min_adx', 25):
                        skip = True
                    if cur_adx > 25:
                        p_val = pdi.iloc[i] if not pd.isna(pdi.iloc[i]) else 0
                        m_val = mdi.iloc[i] if not pd.isna(mdi.iloc[i]) else 0
                        if sw_dir == 'bearish' and m_val < p_val:
                            skip = True
                        if sw_dir == 'bullish' and p_val < m_val:
                            skip = True
                if skip:
                    state = 0; sw_dir = None; continue

                entry = ctr
                if sw_dir == 'bearish':
                    stop = sw_price * 1.003; risk = abs(entry - stop)
                    tp = entry - risk * params['tp_multiplier']; d = 'SELL'
                else:
                    stop = sw_price * 0.997; risk = abs(stop - entry)
                    tp = entry + risk * params['tp_multiplier']; d = 'BUY'
                ts = str(df_part['timestamp'].iloc[i])
                signals.append({
                    'index': i, 'timestamp': ts, 'direction': d,
                    'entry': entry, 'stop': stop, 'tp': tp, 'confidence': 0.7
                })
                state = 0; sw_dir = None
            elif i - sw_idx > params['timeout']:
                state = 0; sw_dir = None

    if len(signals) < 3:
        print(f"  {label}: Only {len(signals)} signals - SKIP")
        return None

    bt_df = df_part.iloc[params['lookback']:].copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]
    if not bs:
        print(f"  {label}: No matching signals")
        return None

    trades, metrics = run_backtest(
        bt_df, bs, initial_balance=10000, risk_percent=1.0,
        breakeven_at=trailing.get('breakeven_at', 0.5),
        trailing_activate=trailing.get('trail_activate', 1.0),
        trailing_step=trailing.get('trail_step', 0.5)
    )

    return {
        'trades': metrics['total_trades'],
        'pf': metrics['profit_factor'],
        'wr': metrics['win_rate'],
        'ret': metrics['total_return'],
        'dd': metrics['max_drawdown'],
    }


n = len(df)
split_half = int(n * 0.5)
split_70 = int(n * 0.7)

print("=" * 55)
print("  OVERFITTING CHECK — WALK FORWARD ANALYSIS")
print("=" * 55)
print(f"  Data: {n} candles ({df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]})")
print(f"  Config: lookback={params['lookback']} sweep={params['sweep_threshold']} "
      f"prox={params['center_proximity']} tp={params['tp_multiplier']} "
      f"ADX={'ON' if filters.get('adx_filter',{}).get('enabled') else 'OFF'}")
print()

# Full
full = run_bt(df, "FULL")
print()
print(f"  {'Period':<28} {'PF':>6} {'WR':>6} {'Return':>8} {'DD':>6} {'Trades':>6}")
print(f"  {'-'*55}")
if full:
    print(f"  {'FULL (12 months)':<28} {full['pf']:>6.2f} {full['wr']:>5.0%} {full['ret']:>+7.2%} {full['dd']:>5.2%} {full['trades']:>6}")

# First half
first = run_bt(df.iloc[:split_half].copy().reset_index(drop=True), "FIRST 50%")
if first:
    print(f"  {'FIRST 50% (train)':<28} {first['pf']:>6.2f} {first['wr']:>5.0%} {first['ret']:>+7.2%} {first['dd']:>5.2%} {first['trades']:>6}")

# Second half
second = run_bt(df.iloc[split_half:].copy().reset_index(drop=True), "SECOND 50%")
if second:
    print(f"  {'SECOND 50% (test)':<28} {second['pf']:>6.2f} {second['wr']:>5.0%} {second['ret']:>+7.2%} {second['dd']:>5.2%} {second['trades']:>6}")

# Last 30%
last = run_bt(df.iloc[split_70:].copy().reset_index(drop=True), "LAST 30%")
if last:
    print(f"  {'LAST 30% (recent)':<28} {last['pf']:>6.2f} {last['wr']:>5.0%} {last['ret']:>+7.2%} {last['dd']:>5.2%} {last['trades']:>6}")

# Mid section (30-70%)
mid = run_bt(df.iloc[split_half:split_70].copy().reset_index(drop=True), "MID 20-70%")
if mid:
    print(f"  {'MID (30-70%)':<28} {mid['pf']:>6.2f} {mid['wr']:>5.0%} {mid['ret']:>+7.2%} {mid['dd']:>5.2%} {mid['trades']:>6}")

print()
print("=" * 55)
print("  VERDICT")
print("=" * 55)

results = [r for r in [full, first, second, last, mid] if r is not None]
if len(results) < 2:
    print("  Not enough data to judge")
else:
    oos = [r for r in [second, last] if r is not None]
    is_fit = [r for r in [full, first] if r is not None]

    avg_is_pf = np.mean([r['pf'] for r in is_fit]) if is_fit else 0
    avg_oos_pf = np.mean([r['pf'] for r in oos]) if oos else 0
    avg_oos_ret = np.mean([r['ret'] for r in oos]) if oos else 0

    print(f"  In-sample avg PF:  {avg_is_pf:.2f}")
    print(f"  Out-of-sample avg PF: {avg_oos_pf:.2f}")
    print(f"  Degradation: {(1 - avg_oos_pf/avg_is_pf)*100:.0f}%" if avg_is_pf > 0 else "")
    print()

    if avg_oos_pf >= 2.0 and avg_oos_ret > 0:
        print("  VERDICT: STRONG — not overfit, config works on unseen data")
    elif avg_oos_pf >= 1.5 and avg_oos_ret > 0:
        print("  VERDICT: GOOD — mild degradation but still profitable")
    elif avg_oos_pf >= 1.0:
        print("  VERDICT: WEAK — marginal edge, consider re-optimization")
    else:
        print("  VERDICT: OVERFIT — fails on unseen data, re-optimize!")
