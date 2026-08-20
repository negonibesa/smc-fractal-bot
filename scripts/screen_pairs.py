"""
Screen all pairs with current BNB best config.
Find top 3 performers (excluding BNB which we already have).
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

DATA_DIR = Path('data/raw')
results = []

for csv_file in sorted(DATA_DIR.glob('*_4h_12m.csv')):
    symbol = csv_file.stem.replace('_4h_12m', '')
    try:
        df = pd.read_csv(csv_file)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        for c in ['open','high','low','close','volume']:
            df[c] = pd.to_numeric(df[c])
        df = df.sort_values('timestamp').reset_index(drop=True)
        if len(df) < 200:
            continue

        center = find_consolidation_center(df, lookback=params['lookback'])
        sweep = detect_sweep(df, center, threshold=params['sweep_threshold'])
        adx, pdi, mdi = None, None, None
        if filters.get('adx_filter', {}).get('enabled'):
            adx, pdi, mdi = calculate_adx(df, period=14)

        signals = []
        state = 0; sw_dir = None; sw_price = None; sw_idx = None; ctr = None

        for i in range(params['lookback'], len(df)):
            c = center.iloc[i] if not pd.isna(center.iloc[i]) else df['close'].iloc[i]
            h, l, cl = df['high'].iloc[i], df['low'].iloc[i], df['close'].iloc[i]
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
                            if sw_dir == 'bearish' and m_val < p_val: skip = True
                            if sw_dir == 'bullish' and p_val < m_val: skip = True
                    if skip:
                        state=0; sw_dir=None; continue
                    entry = ctr
                    if sw_dir == 'bearish':
                        stop=sw_price*1.003; risk=abs(entry-stop)
                        tp=entry-risk*params['tp_multiplier']; d='SELL'
                    else:
                        stop=sw_price*0.997; risk=abs(stop-entry)
                        tp=entry+risk*params['tp_multiplier']; d='BUY'
                    signals.append({'index':i,'timestamp':str(df['timestamp'].iloc[i]),
                                    'direction':d,'entry':entry,'stop':stop,'tp':tp,'confidence':0.7})
                    state=0; sw_dir=None
                elif i - sw_idx > params['timeout']:
                    state=0; sw_dir=None

        if len(signals) < 5:
            continue
        bt_df = df.iloc[params['lookback']:].copy()
        bt_df.index = [str(t) for t in bt_df['timestamp']]
        sd = {s['timestamp']: s for s in signals}
        bs = [sd[ts] for ts in bt_df.index if ts in sd]
        if not bs:
            continue
        trades, metrics = run_backtest(bt_df, bs, initial_balance=10000, risk_percent=1.0,
            breakeven_at=trailing.get('breakeven_at',0.5),
            trailing_activate=trailing.get('trail_activate',1.0),
            trailing_step=trailing.get('trail_step',0.5))
        pf = metrics['profit_factor']
        wr = metrics['win_rate']
        ret = metrics['total_return']
        dd = metrics['max_drawdown']
        t = metrics['total_trades']
        score = pf * max(ret, 0) * np.sqrt(t) if t >= 5 and ret > 0 else 0
        results.append({'symbol':symbol,'candles':len(df),'signals':len(signals),
                        'trades':t,'pf':pf,'wr':wr,'ret':ret,'dd':dd,'score':score})
    except Exception:
        pass

results.sort(key=lambda x: x['score'], reverse=True)

print("=" * 85)
print("  ALL PAIRS SCREENING (BNB config: lb=12 sw=0.008 prox=0.012 tp=1.0 ADX=ON)")
print("=" * 85)
print(f"  {'Symbol':<12} {'Candles':>7} {'Signals':>7} {'Trades':>6} {'PF':>6} {'WR':>6} {'Return':>8} {'DD':>6} {'Score':>8}")
print(f"  {'-'*78}")

for r in results:
    m = " <<<" if r['symbol'] == 'BNBUSDT' else ""
    print(f"  {r['symbol']:<12} {r['candles']:>7} {r['signals']:>7} {r['trades']:>6} {r['pf']:>6.2f} {r['wr']:>5.0%} {r['ret']:>+7.2%} {r['dd']:>5.2%} {r['score']:>8.1f}{m}")

top3 = [r for r in results if r['symbol'] != 'BNBUSDT' and r['score'] > 0][:3]
print(f"\n  TOP 2 (excluding BNB):")
for i, r in enumerate(top3[:2], 1):
    print(f"  {i}. {r['symbol']}: PF={r['pf']:.2f} WR={r['wr']:.0%} Ret={r['ret']:+.2%} DD={r['dd']:.2%} Trades={r['trades']}")
