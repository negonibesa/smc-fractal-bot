"""Quick backtest for XRP and ONDO."""
import warnings; warnings.filterwarnings('ignore')
import yaml, numpy as np, pandas as pd
from pathlib import Path
from backtest import run_backtest
from smc_features import find_consolidation_center, detect_sweep, calculate_adx

with open('config/settings.yaml') as f:
    config = yaml.safe_load(f)

params = config['strategy']
trailing = config.get('trailing', {})

def run_bt(df, p, t):
    lb = p['lookback']
    if len(df) < lb + 10: return None
    center = find_consolidation_center(df, lookback=lb)
    sweep = detect_sweep(df, center, threshold=p['sweep_threshold'])
    adx, pdi, mdi = calculate_adx(df, period=14)
    signals = []; state = 0; sw_dir = None; sw_price = None; sw_idx = None
    for i in range(lb, len(df)):
        c = center.iloc[i] if not pd.isna(center.iloc[i]) else df['close'].iloc[i]
        h, cl = df['high'].iloc[i], df['close'].iloc[i]
        if state == 0:
            if sweep['bearish_sweep'].iloc[i]: state=1; sw_dir='bearish'; sw_price=h; sw_idx=i
            elif sweep['bullish_sweep'].iloc[i]: state=1; sw_dir='bullish'; sw_price=df['low'].iloc[i]; sw_idx=i
        elif state == 1:
            if abs(cl - c) / c < p['center_proximity']:
                cur_adx = adx.iloc[i] if not pd.isna(adx.iloc[i]) else 0
                if cur_adx < 25: state=0; sw_dir=None; continue
                entry = c
                if sw_dir=='bearish':
                    stop=sw_price*1.003; risk=abs(entry-stop)
                    tp=entry-risk*p['tp_multiplier']; d='SELL'
                else:
                    stop=sw_price*0.997; risk=abs(stop-entry)
                    tp=entry+risk*p['tp_multiplier']; d='BUY'
                signals.append({'index':i,'timestamp':str(df['timestamp'].iloc[i]),
                                'direction':d,'entry':entry,'stop':stop,'tp':tp,'confidence':0.7})
                state=0; sw_dir=None
            elif i-sw_idx > p['timeout']: state=0; sw_dir=None
    if len(signals) < 2: return None
    bt = df.iloc[lb:].copy()
    bt.index = [str(t) for t in bt['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt.index if ts in sd]
    if not bs: return None
    _, m = run_backtest(bt, bs, initial_balance=10000, risk_percent=1.0,
        breakeven_at=t.get('breakeven_at',0.5),
        trailing_activate=t.get('trail_activate',1.0),
        trailing_step=t.get('trail_step',0.5))
    return {'pf':m['profit_factor'],'wr':m['win_rate'],'ret':m['total_return'],
            'dd':m['max_drawdown'],'trades':m['total_trades']}

for sym in ['XRPUSDT', 'ONDOUSDT']:
    path = f'data/raw/{sym}_4h_12m.csv'
    if not Path(path).exists():
        print(f'{sym}: NO DATA'); continue
    df = pd.read_csv(path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c])
    df = df.sort_values('timestamp').reset_index(drop=True)

    r = run_bt(df, params, trailing)
    if r:
        print(f'{sym}: PF={r["pf"]:.2f} WR={r["wr"]:.0%} Ret={r["ret"]:+.2%} DD={r["dd"]:.2%} T={r["trades"]} ({len(df)} candles)')
    else:
        print(f'{sym}: no signals ({len(df)} candles)')
