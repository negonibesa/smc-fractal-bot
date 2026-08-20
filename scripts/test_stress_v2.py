"""Stress tests v2: Monte Carlo (real PnL) + Edge Decay (4 windows + buy&hold)."""
import warnings; warnings.filterwarnings('ignore')
import yaml, numpy as np, pandas as pd
from pathlib import Path
from backtest import run_backtest
from smc_features import find_consolidation_center, detect_sweep, calculate_adx

with open('config/settings.yaml') as f:
    config = yaml.safe_load(f)

def get_pair_config(symbol):
    for asset in config.get('assets', []):
        if asset['symbol'] == symbol and 'config' in asset:
            return asset['config']
    return None

def load(symbol, tf='4h'):
    for suffix in ['_12m', '']:
        path = f'data/raw/{symbol}_{tf}{suffix}.csv'
        if Path(path).exists():
            df = pd.read_csv(path)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            for c in ['open','high','low','close','volume']:
                df[c] = pd.to_numeric(df[c])
            return df.sort_values('timestamp').reset_index(drop=True)
    return None

def get_trades_for_symbol(symbol):
    pcfg = get_pair_config(symbol)
    if not pcfg: return None, None, None
    params = pcfg['strategy']
    trail = pcfg.get('trailing', {})
    filters = pcfg.get('filters', {})
    df = load(symbol, '4h')
    df_1d = load(symbol, '1d')
    if df is None: return None, None, None
    flags = {}
    if filters.get('session_filter', {}).get('enabled'): flags['session'] = True
    if filters.get('trend_1d_filter', {}).get('enabled'): flags['trend_1d'] = True

    lb = params['lookback']
    center = find_consolidation_center(df, lookback=lb)
    sweep = detect_sweep(df, center, threshold=params['sweep_threshold'])
    adx, pdi, mdi = calculate_adx(df, period=14)
    daily_ema50 = None
    if flags.get('trend_1d') and df_1d is not None and len(df_1d) >= 50:
        daily_ema50 = df_1d['close'].ewm(span=50).mean()

    signals = []
    state = 0; sw_dir = None; sw_price = None; sw_idx = None
    for i in range(lb, len(df)):
        c = center.iloc[i] if not pd.isna(center.iloc[i]) else df['close'].iloc[i]
        h, l, cl = df['high'].iloc[i], df['low'].iloc[i], df['close'].iloc[i]
        if state == 0:
            if sweep['bearish_sweep'].iloc[i]:
                state=1; sw_dir='bearish'; sw_price=h; sw_idx=i
            elif sweep['bullish_sweep'].iloc[i]:
                state=1; sw_dir='bullish'; sw_price=l; sw_idx=i
        elif state == 1:
            if abs(cl - c) / c < params['center_proximity']:
                cur_adx = adx.iloc[i] if not pd.isna(adx.iloc[i]) else 0
                if cur_adx < 25:
                    state=0; sw_dir=None; continue
                entry = c
                if sw_dir == 'bearish':
                    stop=sw_price*1.003; risk=abs(entry-stop)
                    tp=entry-risk*params['tp_multiplier']; d='SELL'
                else:
                    stop=sw_price*0.997; risk=abs(stop-entry)
                    tp=entry+risk*params['tp_multiplier']; d='BUY'
                if flags.get('session'):
                    ts = df['timestamp'].iloc[i]
                    hour = ts.hour if hasattr(ts, 'hour') else 0
                    if not (8 <= hour <= 21):
                        state=0; sw_dir=None; continue
                if flags.get('trend_1d') and daily_ema50 is not None:
                    ts_4h = df['timestamp'].iloc[i]
                    day_mask = df_1d['timestamp'].dt.date == ts_4h.date()
                    day_idx = df_1d.index[day_mask]
                    if len(day_idx) > 0:
                        di = day_idx[0]
                        ema_val = daily_ema50.iloc[di] if di < len(daily_ema50) else None
                        if ema_val is not None and not pd.isna(ema_val):
                            close_1d = df_1d['close'].iloc[di]
                            bullish = close_1d > ema_val
                            if d == 'SELL' and bullish:
                                state=0; sw_dir=None; continue
                            if d == 'BUY' and not bullish:
                                state=0; sw_dir=None; continue
                signals.append({'index':i,'timestamp':str(df['timestamp'].iloc[i]),
                                'direction':d,'entry':entry,'stop':stop,'tp':tp})
                state=0; sw_dir=None
            elif i - sw_idx > params['timeout']:
                state=0; sw_dir=None

    if len(signals) < 2:
        return None, None, df

    bt_df = df.iloc[lb:].copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]
    if not bs:
        return None, None, df

    trades, metrics = run_backtest(bt_df, bs, initial_balance=10000, risk_percent=1.5,
        slippage=0.0005,
        breakeven_at=trail.get('breakeven_at',0.5),
        trailing_activate=trail.get('trail_activate',1.0),
        trailing_step=trail.get('trail_step',0.5),
        max_leverage=20,
        dynamic_risk={'base_risk':1.5,'min_risk':0.75,'max_risk':2.0,
                      'dd_threshold_1':6.0,'dd_threshold_2':10.0,'pf_hot':2.0,'pf_window':20})
    return trades, metrics, df


# ═══════════════════════════════════════════════════════════════════
# 1. MONTE CARLO (REAL PnL)
# ═══════════════════════════════════════════════════════════════════
print("=" * 70)
print("  1. MONTE CARLO (REAL PnL DISTRIBUTION)")
print("=" * 70)

all_pnls = []
for symbol in ['BNBUSDT', 'RENDERUSDT', 'XRPUSDT']:
    trades, metrics, df = get_trades_for_symbol(symbol)
    if trades:
        for t in trades:
            all_pnls.append(t['pnl'])

print(f"  Total trades: {len(all_pnls)}")
print(f"  Avg PnL: ${np.mean(all_pnls):.2f}")
print(f"  Median: ${np.median(all_pnls):.2f}")
print(f"  Win avg: ${np.mean([p for p in all_pnls if p > 0]):.2f}")
print(f"  Loss avg: ${np.mean([p for p in all_pnls if p <= 0]):.2f}")

mc_results = []
np.random.seed(42)
for _ in range(1000):
    sampled = np.random.choice(all_pnls, size=len(all_pnls), replace=True)
    balance = 30000
    peak = balance
    max_dd = 0
    for pnl in sampled:
        balance += pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100
        max_dd = max(max_dd, dd)
    mc_results.append({'final': balance, 'dd': max_dd, 'return': (balance - 30000) / 30000 * 100})

mc_df = pd.DataFrame(mc_results)
print(f"\n  {'Percentile':<12} {'Final Balance':>14} {'Return':>10} {'Max DD':>10}")
print(f"  {'─' * 50}")
for p in [5, 25, 50, 75, 95]:
    row = mc_df.quantile(p / 100)
    print(f"  {p:>3}th        ${row['final']:>11,.0f}  {row['return']:>+8.1f}%  {row['dd']:>8.1f}%")

print(f"\n  Mean:   ${mc_df['final'].mean():,.0f} ({mc_df['return'].mean():+.1f}%)")
print(f"  Worst:  ${mc_df['final'].min():,.0f} ({mc_df['return'].min():+.1f}%)")
print(f"  Best:   ${mc_df['final'].max():,.0f} ({mc_df['return'].max():+.1f}%)")
print(f"  Prob of loss: {(mc_df['final'] < 30000).mean()*100:.1f}%")
print(f"  Prob of DD>15%: {(mc_df['dd'] > 15).mean()*100:.1f}%")


# ═══════════════════════════════════════════════════════════════════
# 2. EDGE DECAY (4 WINDOWS)
# ═══════════════════════════════════════════════════════════════════
print(f"\n\n{'=' * 70}")
print("  2. EDGE DECAY — 4 WINDOWS (25% each)")
print("=" * 70)

for symbol in ['BNBUSDT', 'RENDERUSDT', 'XRPUSDT']:
    pcfg = get_pair_config(symbol)
    if not pcfg: continue
    df = load(symbol, '4h')
    df_1d = load(symbol, '1d')
    if df is None: continue
    params = pcfg['strategy']
    trail = pcfg.get('trailing', {})
    filters = pcfg.get('filters', {})
    flags = {}
    if filters.get('session_filter', {}).get('enabled'): flags['session'] = True
    if filters.get('trend_1d_filter', {}).get('enabled'): flags['trend_1d'] = True

    n = len(df)
    q = n // 4
    windows = [
        ("Q1 (0-25%)", df.iloc[:q].reset_index(drop=True)),
        ("Q2 (25-50%)", df.iloc[q:2*q].reset_index(drop=True)),
        ("Q3 (50-75%)", df.iloc[2*q:3*q].reset_index(drop=True)),
        ("Q4 (75-100%)", df.iloc[3*q:].reset_index(drop=True)),
    ]

    print(f"\n  {symbol}:")
    pfs = []
    for name, wdf in windows:
        lb = params['lookback']
        if len(wdf) < lb + 10:
            print(f"    {name}: too short ({len(wdf)} candles)")
            continue
        center = find_consolidation_center(wdf, lookback=lb)
        sweep = detect_sweep(wdf, center, threshold=params['sweep_threshold'])
        adx, _, _ = calculate_adx(wdf, period=14)
        daily_ema50 = None
        if flags.get('trend_1d') and df_1d is not None and len(df_1d) >= 50:
            daily_ema50 = df_1d['close'].ewm(span=50).mean()

        sigs = []
        state = 0; sw_dir = None; sw_price = None; sw_idx = None
        for i in range(lb, len(wdf)):
            c = center.iloc[i] if not pd.isna(center.iloc[i]) else wdf['close'].iloc[i]
            h, l, cl = wdf['high'].iloc[i], wdf['low'].iloc[i], wdf['close'].iloc[i]
            if state == 0:
                if sweep['bearish_sweep'].iloc[i]:
                    state=1; sw_dir='bearish'; sw_price=h; sw_idx=i
                elif sweep['bullish_sweep'].iloc[i]:
                    state=1; sw_dir='bullish'; sw_price=l; sw_idx=i
            elif state == 1:
                if abs(cl - c) / c < params['center_proximity']:
                    cur_adx = adx.iloc[i] if not pd.isna(adx.iloc[i]) else 0
                    if cur_adx < 25:
                        state=0; sw_dir=None; continue
                    entry = c
                    if sw_dir == 'bearish':
                        stop=sw_price*1.003; risk=abs(entry-stop)
                        tp=entry-risk*params['tp_multiplier']; d='SELL'
                    else:
                        stop=sw_price*0.997; risk=abs(stop-entry)
                        tp=entry+risk*params['tp_multiplier']; d='BUY'
                    if flags.get('session'):
                        ts = wdf['timestamp'].iloc[i]
                        hour = ts.hour if hasattr(ts, 'hour') else 0
                        if not (8 <= hour <= 21):
                            state=0; sw_dir=None; continue
                    if flags.get('trend_1d') and daily_ema50 is not None:
                        ts_4h = wdf['timestamp'].iloc[i]
                        day_mask = df_1d['timestamp'].dt.date == ts_4h.date()
                        day_idx = df_1d.index[day_mask]
                        if len(day_idx) > 0:
                            di = day_idx[0]
                            ema_val = daily_ema50.iloc[di] if di < len(daily_ema50) else None
                            if ema_val is not None and not pd.isna(ema_val):
                                close_1d = df_1d['close'].iloc[di]
                                bullish = close_1d > ema_val
                                if d == 'SELL' and bullish:
                                    state=0; sw_dir=None; continue
                                if d == 'BUY' and not bullish:
                                    state=0; sw_dir=None; continue
                    sigs.append({'index':i,'timestamp':str(wdf['timestamp'].iloc[i]),
                                'direction':d,'entry':entry,'stop':stop,'tp':tp})
                    state=0; sw_dir=None
                elif i - sw_idx > params['timeout']:
                    state=0; sw_dir=None

        if len(sigs) < 2:
            print(f"    {name}: {len(sigs)} signals (too few)")
            continue

        bt_df = wdf.iloc[lb:].copy()
        bt_df.index = [str(t) for t in bt_df['timestamp']]
        sd = {s['timestamp']: s for s in sigs}
        bs = [sd[ts] for ts in bt_df.index if ts in sd]
        if not bs:
            print(f"    {name}: no matched signals")
            continue

        _, m = run_backtest(bt_df, bs, initial_balance=10000, risk_percent=1.5, max_leverage=20,
            breakeven_at=trail.get('breakeven_at',0.5),
            trailing_activate=trail.get('trail_activate',1.0),
            trailing_step=trail.get('trail_step',0.5))
        pf = m['profit_factor']
        pfs.append(pf)
        status = "✓" if pf > 1.5 else ("⚠" if pf > 1.0 else "✗")
        print(f"    {name}: PF={pf:.2f}  Trades={m['total_trades']}  WR={m['win_rate']:.0%}  Ret={m['total_return']:+.1%}  {status}")

    if len(pfs) >= 2:
        trend = "↓ DECAYING" if pfs[-1] < pfs[0] * 0.5 else ("→ STABLE" if pfs[-1] > pfs[0] * 0.7 else "↑ RECOVERING")
        print(f"    Trend: {pfs[0]:.2f} → {pfs[-1]:.2f}  {trend}")


# ═══════════════════════════════════════════════════════════════════
# 3. BUY & HOLD COMPARISON
# ═══════════════════════════════════════════════════════════════════
print(f"\n\n{'=' * 70}")
print("  3. BUY & HOLD vs STRATEGY")
print("=" * 70)

for symbol in ['BNBUSDT', 'RENDERUSDT', 'XRPUSDT']:
    df = load(symbol, '4h')
    if df is None: continue
    
    start_price = df['close'].iloc[0]
    end_price = df['close'].iloc[-1]
    bh_return = (end_price - start_price) / start_price * 100
    
    trades, metrics, _ = get_trades_for_symbol(symbol)
    strat_return = metrics['total_return'] * 100 if metrics else 0
    
    winner = "STRATEGY" if strat_return > bh_return else "BUY&HOLD"
    print(f"  {symbol:<14} Buy&Hold: {bh_return:+.1f}%  Strategy: {strat_return:+.1f}%  Winner: {winner}")

print(f"\n{'=' * 70}")
print("  ALL TESTS COMPLETE")
print(f"{'=' * 70}")
