"""
Walk-forward check for FIL and RENDER.
"""
import warnings; warnings.filterwarnings('ignore')
import yaml, numpy as np, pandas as pd
from pathlib import Path
from backtest import run_backtest
from smc_features import find_consolidation_center, detect_sweep, calculate_adx

with open('config/best.yaml') as f:
    config = yaml.safe_load(f)
params = config['strategy']
filters = config['filters']
trailing = config['trailing']

def run_bt(df_part):
    center = find_consolidation_center(df_part, lookback=params['lookback'])
    sweep = detect_sweep(df_part, center, threshold=params['sweep_threshold'])
    adx, pdi, mdi = None, None, None
    if filters.get('adx_filter', {}).get('enabled'):
        adx, pdi, mdi = calculate_adx(df_part, period=14)
    signals = []
    state = 0; sw_dir = None; sw_price = None; sw_idx = None; ctr = None
    for i in range(params['lookback'], len(df_part)):
        c = center.iloc[i] if not pd.isna(center.iloc[i]) else df_part['close'].iloc[i]
        h, l, cl = df_part['high'].iloc[i], df_part['low'].iloc[i], df_part['close'].iloc[i]
        has_bull = sweep['bullish_sweep'].iloc[i] if i < len(sweep['bullish_sweep']) else False
        has_bear = sweep['bearish_sweep'].iloc[i] if i < len(sweep['bearish_sweep']) else False
        if state == 0:
            if has_bear: state=1; sw_dir='bearish'; sw_price=h; sw_idx=i; ctr=c
            elif has_bull: state=1; sw_dir='bullish'; sw_price=l; sw_idx=i; ctr=c
        elif state == 1:
            if abs(cl - ctr) / ctr < params['center_proximity']:
                skip = False
                if adx is not None:
                    cur_adx = adx.iloc[i] if not pd.isna(adx.iloc[i]) else 0
                    if cur_adx < filters['adx_filter'].get('min_adx', 25): skip = True
                    if cur_adx > 25:
                        p_val = pdi.iloc[i] if not pd.isna(pdi.iloc[i]) else 0
                        m_val = mdi.iloc[i] if not pd.isna(mdi.iloc[i]) else 0
                        if sw_dir == 'bearish' and m_val < p_val: skip = True
                        if sw_dir == 'bullish' and p_val < m_val: skip = True
                if skip: state=0; sw_dir=None; continue
                entry = ctr
                if sw_dir == 'bearish':
                    stop=sw_price*1.003; risk=abs(entry-stop)
                    tp=entry-risk*params['tp_multiplier']; d='SELL'
                else:
                    stop=sw_price*0.997; risk=abs(stop-entry)
                    tp=entry+risk*params['tp_multiplier']; d='BUY'
                signals.append({'index':i,'timestamp':str(df_part['timestamp'].iloc[i]),
                                'direction':d,'entry':entry,'stop':stop,'tp':tp,'confidence':0.7})
                state=0; sw_dir=None
            elif i - sw_idx > params['timeout']: state=0; sw_dir=None
    if len(signals) < 3: return None
    bt_df = df_part.iloc[params['lookback']:].copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]
    if not bs: return None
    _, metrics = run_backtest(bt_df, bs, initial_balance=10000, risk_percent=1.0,
        breakeven_at=trailing.get('breakeven_at',0.5),
        trailing_activate=trailing.get('trail_activate',1.0),
        trailing_step=trailing.get('trail_step',0.5))
    return {'pf':metrics['profit_factor'],'wr':metrics['win_rate'],
            'ret':metrics['total_return'],'dd':metrics['max_drawdown'],'trades':metrics['total_trades']}

def load(symbol):
    df = pd.read_csv(f'data/raw/{symbol}_4h_12m.csv')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    for c in ['open','high','low','close','volume']: df[c] = pd.to_numeric(df[c])
    return df.sort_values('timestamp').reset_index(drop=True)

for symbol in ['FILUSDT', 'RENDERUSDT']:
    df = load(symbol)
    n = len(df)
    s50 = int(n * 0.5)
    s70 = int(n * 0.7)

    full = run_bt(df)
    first = run_bt(df.iloc[:s50].copy().reset_index(drop=True))
    second = run_bt(df.iloc[s50:].copy().reset_index(drop=True))
    last = run_bt(df.iloc[s70:].copy().reset_index(drop=True))

    print(f"\n{'='*55}")
    print(f"  {symbol} — WALK FORWARD ({n} candles)")
    print(f"{'='*55}")
    print(f"  {'Period':<28} {'PF':>6} {'WR':>6} {'Return':>8} {'DD':>6} {'T':>4}")
    print(f"  {'-'*55}")
    for label, r in [('FULL', full), ('FIRST 50% (train)', first),
                      ('SECOND 50% (test)', second), ('LAST 30% (recent)', last)]:
        if r:
            print(f"  {label:<28} {r['pf']:>6.2f} {r['wr']:>5.0%} {r['ret']:>+7.2%} {r['dd']:>5.2%} {r['trades']:>4}")

    oos = [r for r in [second, last] if r]
    is_fit = [r for r in [full, first] if r]
    if oos and is_fit:
        avg_oos = np.mean([r['pf'] for r in oos])
        avg_is = np.mean([r['pf'] for r in is_fit])
        avg_oos_ret = np.mean([r['ret'] for r in oos])
        print(f"\n  IS avg PF: {avg_is:.2f} | OOS avg PF: {avg_oos:.2f} | Ret: {avg_oos_ret:+.2%}")
        if avg_oos >= 2.0 and avg_oos_ret > 0:
            print(f"  VERDICT: ROBUST")
        elif avg_oos >= 1.5:
            print(f"  VERDICT: GOOD (mild degradation)")
        elif avg_oos >= 1.0:
            print(f"  VERDICT: WEAK")
        else:
            print(f"  VERDICT: OVERFIT")
