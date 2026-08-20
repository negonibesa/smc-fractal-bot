"""4-year backtest — all pairs."""
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
    # Fallback: default config
    return {
        'strategy': {'lookback': 12, 'sweep_threshold': 0.008, 'center_proximity': 0.012, 'tp_multiplier': 1.0, 'timeout': 15},
        'filters': {'adx_filter': {'enabled': True, 'min_adx': 25}},
        'trailing': {'enabled': True, 'breakeven_at': 0.5, 'trail_activate': 1.0, 'trail_step': 0.5},
    }

def load(symbol, tf='4h'):
    for suffix in ['_4y', '_12m', '']:
        path = f'data/raw/{symbol}_{tf}{suffix}.csv'
        if Path(path).exists():
            df = pd.read_csv(path)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            for c in ['open','high','low','close','volume']:
                df[c] = pd.to_numeric(df[c])
            return df.sort_values('timestamp').reset_index(drop=True)
    return None

def run_bt(df, params, trailing, filters_flags=None, df_1d=None):
    lb = params['lookback']
    if len(df) < lb + 10:
        return None, None

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

    _, metrics = run_backtest(bt_df, bs, initial_balance=10000, risk_percent=1.5,
        slippage=0.0005,
        breakeven_at=trailing.get('breakeven_at',0.5),
        trailing_activate=trailing.get('trail_activate',1.0),
        trailing_step=trailing.get('trail_step',0.5),
        max_leverage=20,
        dynamic_risk={'base_risk':1.5,'min_risk':0.75,'max_risk':2.0,
                      'dd_threshold_1':6.0,'dd_threshold_2':10.0,'pf_hot':2.0,'pf_window':20})
    return metrics, signals


# ─── RUN ──────────────────────────────────────────────────────
print("=" * 80)
print("  4-YEAR BACKTEST — ALL PAIRS")
print("=" * 80)

# SOL and LINK get default config (not in settings.yaml)
default_cfg = {
    'strategy': {'lookback': 12, 'sweep_threshold': 0.008, 'center_proximity': 0.012, 'tp_multiplier': 1.0, 'timeout': 15},
    'filters': {'adx_filter': {'enabled': True, 'min_adx': 25}},
    'trailing': {'enabled': True, 'breakeven_at': 0.5, 'trail_activate': 1.0, 'trail_step': 0.5},
}

total_pnl = 0
total_trades = 0
results = []

for symbol in ['BNBUSDT', 'RENDERUSDT', 'XRPUSDT', 'SOLUSDT', 'LINKUSDT']:
    pcfg = get_pair_config(symbol) or default_cfg
    params = pcfg['strategy']
    trail = pcfg.get('trailing', {})
    filters = pcfg.get('filters', {})
    df = load(symbol, '4h')
    df_1d = load(symbol, '1d')
    
    if df is None:
        print(f"\n{symbol}: NO DATA")
        continue

    flags = {}
    if filters.get('session_filter', {}).get('enabled'): flags['session'] = True
    if filters.get('trend_1d_filter', {}).get('enabled'): flags['trend_1d'] = True

    metrics, signals = run_bt(df, params, trail, flags, df_1d)

    period_start = df['timestamp'].min().strftime('%Y-%m-%d')
    period_end = df['timestamp'].max().strftime('%Y-%m-%d')
    months = (df['timestamp'].max() - df['timestamp'].min()).days / 30

    if metrics:
        pf = metrics['profit_factor']
        wr = metrics['win_rate']
        ret = metrics['total_return']
        dd = metrics['max_drawdown']
        trades = metrics['total_trades']
        pnl = metrics['final_balance'] - 10000
        wins = int(trades * wr)
        losses = trades - wins
        annual_ret = ret * 12 / months if months > 0 else 0

        total_pnl += pnl
        total_trades += trades

        filter_str = ", ".join(f for f in flags) if flags else "ADX only"
        
        print(f"\n{'─' * 80}")
        print(f"  {symbol}")
        print(f"{'─' * 80}")
        print(f"  Period:    {period_start} → {period_end} ({months:.0f} months)")
        print(f"  Filters:   {filter_str}")
        print(f"  Trades:    {trades} (W:{wins} / L:{losses})")
        print(f"  Win Rate:  {wr:.0%}")
        print(f"  PF:        {pf:.2f}")
        print(f"  Return:    {ret:+.2%}")
        print(f"  Annual:    {annual_ret:+.2%}")
        print(f"  PnL:       ${pnl:+,.2f}")
        print(f"  Max DD:    {dd:.2%}")

        results.append({'symbol': symbol, 'trades': trades, 'pf': pf, 'wr': wr,
                        'ret': ret, 'annual': annual_ret, 'pnl': pnl, 'dd': dd,
                        'months': months, 'start': period_start, 'end': period_end})

# ─── PER-YEAR BREAKDOWN ──────────────────────────────────────
print(f"\n\n{'=' * 80}")
print("  PER-YEAR BREAKDOWN")
print("=" * 80)

for symbol in ['BNBUSDT', 'XRPUSDT', 'SOLUSDT', 'LINKUSDT']:
    df = load(symbol, '4h')
    df_1d = load(symbol, '1d')
    if df is None: continue
    pcfg = get_pair_config(symbol) or default_cfg
    params = pcfg['strategy']
    trail = pcfg.get('trailing', {})
    filters = pcfg.get('filters', {})
    flags = {}
    if filters.get('session_filter', {}).get('enabled'): flags['session'] = True
    if filters.get('trend_1d_filter', {}).get('enabled'): flags['trend_1d'] = True

    years = sorted(df['timestamp'].dt.year.unique())
    print(f"\n  {symbol}:")
    for year in years:
        mask = df['timestamp'].dt.year == year
        ydf = df[mask].reset_index(drop=True)
        if len(ydf) < 50:
            print(f"    {year}: {len(ydf)} candles (too short)")
            continue
        m, s = run_bt(ydf, params, trail, flags, df_1d)
        if m:
            status = "✓" if m['profit_factor'] > 1.5 else ("⚠" if m['profit_factor'] > 1.0 else "✗")
            print(f"    {year}: PF={m['profit_factor']:.2f}  Trades={m['total_trades']}  "
                  f"WR={m['win_rate']:.0%}  Ret={m['total_return']:+.1%}  DD={m['max_drawdown']:.1%}  {status}")
        else:
            print(f"    {year}: no signals")

# ─── SUMMARY ─────────────────────────────────────────────────
print(f"\n\n{'=' * 80}")
print("  PORTFOLIO SUMMARY (4 YEARS)")
print("=" * 80)

if results:
    print(f"\n  {'Pair':<14} {'Months':>7} {'Trades':>7} {'PF':>8} {'WR':>6} {'Return':>10} {'Annual':>10} {'PnL':>12} {'DD':>8}")
    print(f"  {'─' * 82}")
    for r in results:
        print(f"  {r['symbol']:<14} {r['months']:>6.0f}mo {r['trades']:>7} {r['pf']:>8.2f} "
              f"{r['wr']:>5.0%} {r['ret']:>+9.2%} {r['annual']:>+9.2%} ${r['pnl']:>+10.2f} {r['dd']:>7.2%}")
    print(f"  {'─' * 82}")
    avg_annual = np.mean([r['annual'] for r in results])
    print(f"  {'TOTAL':<14} {'':>7} {total_trades:>7} {'':>8} {'':>6} {'':>10} {avg_annual:>+9.2%} ${total_pnl:>+10.2f}")
    print(f"\n  Average annual return: {avg_annual:+.2%}")
