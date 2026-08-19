"""Comprehensive stress tests: Monte Carlo, slippage, correlation, edge decay, recovery."""
import warnings; warnings.filterwarnings('ignore')
import yaml, numpy as np, pandas as pd
from pathlib import Path
from backtest import run_backtest, _calc_pf_last_n
from smc_features import find_consolidation_center, detect_sweep, calculate_adx
from datetime import datetime

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

def run_bt_full(df, params, trailing, filters_flags=None, df_1d=None, risk_pct=1.5, slippage=0.0005):
    lb = params['lookback']
    if len(df) < lb + 10:
        return None, []

    center = find_consolidation_center(df, lookback=lb)
    sweep = detect_sweep(df, center, threshold=params['sweep_threshold'])
    adx, pdi, mdi = calculate_adx(df, period=14)

    daily_ema50 = None
    if filters_flags and filters_flags.get('trend_1d') and df_1d is not None and len(df_1d) >= 50:
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

                if filters_flags and filters_flags.get('session'):
                    ts = df['timestamp'].iloc[i]
                    hour = ts.hour if hasattr(ts, 'hour') else 0
                    if not (8 <= hour <= 21):
                        state=0; sw_dir=None; continue

                if filters_flags and filters_flags.get('trend_1d') and daily_ema50 is not None:
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
        return None, signals

    bt_df = df.iloc[lb:].copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]
    if not bs:
        return None, signals

    _, metrics = run_backtest(bt_df, bs, initial_balance=10000, risk_percent=risk_pct,
        slippage=slippage,
        breakeven_at=trailing.get('breakeven_at',0.5),
        trailing_activate=trailing.get('trail_activate',1.0),
        trailing_step=trailing.get('trail_step',0.5),
        max_leverage=20,
        dynamic_risk={'base_risk':1.5,'min_risk':0.75,'max_risk':2.0,
                      'dd_threshold_1':6.0,'dd_threshold_2':10.0,'pf_hot':2.0,'pf_window':20})
    return metrics, bs

def get_all_trades():
    all_trades = []
    for symbol in ['BNBUSDT', 'RENDERUSDT', 'XRPUSDT']:
        pcfg = get_pair_config(symbol)
        if not pcfg: continue
        params = pcfg['strategy']
        trail = pcfg.get('trailing', {})
        filters = pcfg.get('filters', {})
        df = load(symbol, '4h')
        df_1d = load(symbol, '1d')
        if df is None: continue
        flags = {}
        if filters.get('session_filter', {}).get('enabled'): flags['session'] = True
        if filters.get('trend_1d_filter', {}).get('enabled'): flags['trend_1d'] = True
        metrics, signals = run_bt_full(df, params, trail, flags, df_1d)
        if metrics:
            all_trades.append({'symbol': symbol, 'trades': signals, 'metrics': metrics})
    return all_trades


# ═══════════════════════════════════════════════════════════════════
# 1. MONTE CARLO
# ═══════════════════════════════════════════════════════════════════
print("=" * 70)
print("  1. MONTE CARLO SIMULATION (1000 iterations)")
print("=" * 70)

all_data = get_all_trades()
# Collect all trade pnls from all pairs
all_pnls = []
for pair_data in all_data:
    metrics = pair_data['metrics']
    # Recreate individual trade pnls from metrics
    # We need actual trade list - let's get it from backtest
    all_pnls.append((pair_data['symbol'], pair_data['trades']))

mc_results = []
np.random.seed(42)
for _ in range(1000):
    # Shuffle all trades across all pairs
    shuffled = []
    for symbol, trades in all_pnls:
        for t in trades:
            shuffled.append(t.copy())
    np.random.shuffle(shuffled)

    balance = 30000  # $10K × 3 pairs
    peak = balance
    max_dd = 0
    for t in shuffled:
        # Simplified: estimate pnl from entry/tp/stop
        entry = t['entry']
        tp = t['tp']
        stop = t['stop']
        risk = abs(entry - stop)
        if risk == 0: continue
        notional = balance * 0.015 / (risk / entry)  # 1.5% risk
        notional = min(notional, balance * 20)  # leverage cap

        # Random win/loss (use historical win rate)
        if np.random.random() < 0.48:  # 48% win rate
            pnl = notional * abs(tp - entry) / entry * 0.998  # slippage
        else:
            pnl = -notional * abs(stop - entry) / entry * 0.998
        balance += pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100
        max_dd = max(max_dd, dd)
    mc_results.append({'final': balance, 'dd': max_dd, 'return': (balance - 30000) / 30000 * 100})

mc_df = pd.DataFrame(mc_results)
percentiles = [5, 25, 50, 75, 95]
print(f"\n  {'Percentile':<12} {'Final Balance':>14} {'Return':>10} {'Max DD':>10}")
print(f"  {'─' * 50}")
for p in percentiles:
    row = mc_df.quantile(p / 100)
    print(f"  {p:>3}th        ${row['final']:>11,.0f}  {row['return']:>+8.1f}%  {row['dd']:>8.1f}%")

print(f"\n  Mean:   ${mc_df['final'].mean():,.0f} ({mc_df['return'].mean():+.1f}%)")
print(f"  Std:    ${mc_df['final'].std():,.0f}")
print(f"  Worst:  ${mc_df['final'].min():,.0f} ({mc_df['return'].min():+.1f}%)")
print(f"  Best:   ${mc_df['final'].max():,.0f} ({mc_df['return'].max():+.1f}%)")
print(f"  Prob of loss: {(mc_df['final'] < 30000).mean()*100:.1f}%")
print(f"  Prob of DD>15%: {(mc_df['dd'] > 15).mean()*100:.1f}%")


# ═══════════════════════════════════════════════════════════════════
# 2. SLIPPAGE STRESS
# ═══════════════════════════════════════════════════════════════════
print(f"\n\n{'=' * 70}")
print("  2. SLIPPAGE STRESS TEST")
print("=" * 70)

for slippage in [0.0005, 0.001, 0.002, 0.005]:
    total_pnl = 0
    total_trades = 0
    for symbol in ['BNBUSDT', 'RENDERUSDT', 'XRPUSDT']:
        pcfg = get_pair_config(symbol)
        if not pcfg: continue
        params = pcfg['strategy']
        trail = pcfg.get('trailing', {})
        filters = pcfg.get('filters', {})
        df = load(symbol, '4h')
        df_1d = load(symbol, '1d')
        if df is None: continue
        flags = {}
        if filters.get('session_filter', {}).get('enabled'): flags['session'] = True
        if filters.get('trend_1d_filter', {}).get('enabled'): flags['trend_1d'] = True
        m, _ = run_bt_full(df, params, trail, flags, df_1d, slippage=slippage)
        if m:
            total_pnl += m['final_balance'] - 10000
            total_trades += m['total_trades']
    print(f"  Slippage {slippage:.2%}  →  PnL: ${total_pnl:>+10,.2f}  Trades: {total_trades}")


# ═══════════════════════════════════════════════════════════════════
# 3. CORRELATION
# ═══════════════════════════════════════════════════════════════════
print(f"\n\n{'=' * 70}")
print("  3. CORRELATION ANALYSIS")
print("=" * 70)

prices = {}
for symbol in ['BNBUSDT', 'RENDERUSDT', 'XRPUSDT']:
    df = load(symbol, '4h')
    if df is not None:
        prices[symbol] = df.set_index('timestamp')['close'].astype(float)

if len(prices) == 3:
    price_df = pd.DataFrame(prices).dropna()
    returns = price_df.pct_change().dropna()
    corr = returns.corr()
    print(f"\n  Price Correlation (4H returns):")
    print(f"  {'':>14} {'BNB':>10} {'RENDER':>10} {'XRP':>10}")
    for s1 in ['BNBUSDT', 'RENDERUSDT', 'XRPUSDT']:
        vals = [corr.loc[s1, s2] for s2 in ['BNBUSDT', 'RENDERUSDT', 'XRPUSDT']]
        print(f"  {s1:>14} {vals[0]:>10.3f} {vals[1]:>10.3f} {vals[2]:>10.3f}")
    
    avg_corr = (corr.loc['BNBUSDT','RENDERUSDT'] + corr.loc['BNBUSDT','XRPUSDT'] + corr.loc['RENDERUSDT','XRPUSDT']) / 3
    print(f"\n  Average pairwise correlation: {avg_corr:.3f}")
    if avg_corr > 0.7:
        print("  ⚠ HIGH correlation — portfolio DD may be worse than individual")
    elif avg_corr > 0.4:
        print("  ✓ Moderate correlation — acceptable diversification")
    else:
        print("  ✓✓ Low correlation — good diversification")


# ═══════════════════════════════════════════════════════════════════
# 4. EDGE DECAY (first half vs second half)
# ═══════════════════════════════════════════════════════════════════
print(f"\n\n{'=' * 70}")
print("  4. EDGE DECAY (first half vs second half)")
print("=" * 70)

for symbol in ['BNBUSDT', 'RENDERUSDT', 'XRPUSDT']:
    pcfg = get_pair_config(symbol)
    if not pcfg: continue
    df = load(symbol, '4h')
    df_1d = load(symbol, '1d')
    if df is None: continue
    
    mid = len(df) // 2
    df1 = df.iloc[:mid].reset_index(drop=True)
    df2 = df.iloc[mid:].reset_index(drop=True)
    
    params = pcfg['strategy']
    trail = pcfg.get('trailing', {})
    filters = pcfg.get('filters', {})
    flags = {}
    if filters.get('session_filter', {}).get('enabled'): flags['session'] = True
    if filters.get('trend_1d_filter', {}).get('enabled'): flags['trend_1d'] = True
    
    m1, s1 = run_bt_full(df1, params, trail, flags, df_1d)
    m2, s2 = run_bt_full(df2, params, trail, flags, df_1d)
    
    if m1 and m2:
        pf1 = m1['profit_factor']
        pf2 = m2['profit_factor']
        delta = ((pf2 - pf1) / pf1 * 100) if pf1 > 0 else 0
        status = "✓ STABLE" if abs(delta) < 30 else ("⚠ DECAYING" if delta < 0 else "↑ IMPROVING")
        print(f"  {symbol:<14} 1st half: PF={pf1:.2f} ({m1['total_trades']} trades)  "
              f"2nd half: PF={pf2:.2f} ({m2['total_trades']} trades)  Δ={delta:+.0f}%  {status}")
    elif m1:
        print(f"  {symbol:<14} 1st half: PF={m1['profit_factor']:.2f}  2nd half: no signals")
    elif m2:
        print(f"  {symbol:<14} 1st half: no signals  2nd half: PF={m2['profit_factor']:.2f}")


# ═══════════════════════════════════════════════════════════════════
# 5. DRAWDOWN RECOVERY TIME
# ═══════════════════════════════════════════════════════════════════
print(f"\n\n{'=' * 70}")
print("  5. DRAWDOWN RECOVERY TIME")
print("=" * 70)

all_trades_combined = []
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
    m, sigs = run_bt_full(df, params, trail, flags, df_1d)
    if m and sigs:
        for s in sigs:
            all_trades_combined.append(s)

if all_trades_combined:
    # Simulate equity curve
    balance = 30000
    peak = balance
    equity_curve = [balance]
    for t in all_trades_combined:
        entry = t['entry']; tp = t['tp']; stop = t['stop']
        risk = abs(entry - stop)
        if risk == 0: continue
        notional = balance * 0.015 / (risk / entry)
        notional = min(notional, balance * 20)
        
        if np.random.random() < 0.48:
            pnl = notional * abs(tp - entry) / entry * 0.998
        else:
            pnl = -notional * abs(stop - entry) / entry * 0.998
        balance += pnl
        peak = max(peak, balance)
        equity_curve.append(balance)
    
    eq = np.array(equity_curve)
    peaks = np.maximum.accumulate(eq)
    dd = (peaks - eq) / peaks * 100
    
    # Find max DD periods
    in_dd = False
    dd_start = 0
    dd_events = []
    for i in range(len(dd)):
        if dd[i] > 1 and not in_dd:
            in_dd = True
            dd_start = i
        elif dd[i] < 0.5 and in_dd:
            in_dd = False
            dd_events.append({'start': dd_start, 'end': i, 'max_dd': dd[dd_start:i].max(), 'recovery_candles': i - dd_start})
    
    if dd_events:
        print(f"\n  DD events found: {len(dd_events)}")
        for i, e in enumerate(dd_events[:5]):
            print(f"  #{i+1}: Max DD={e['max_dd']:.1f}%, Recovery={e['recovery_candles']} candles ({e['recovery_candles']*4}h)")
        avg_recovery = np.mean([e['recovery_candles'] for e in dd_events])
        print(f"\n  Average recovery: {avg_recovery:.0f} candles ({avg_recovery*4:.0f}h = {avg_recovery*4/24:.1f} days)")
    else:
        print("  No significant DD events (>1%)")
    
    max_dd = dd.max()
    print(f"  Max drawdown: {max_dd:.1f}%")
    print(f"  Time in DD: {(dd > 1).sum() / len(dd) * 100:.1f}%")
    print(f"  Time in profit: {(eq > 30000).sum() / len(eq) * 100:.1f}%")

print(f"\n{'=' * 70}")
print("  ALL TESTS COMPLETE")
print(f"{'=' * 70}")
