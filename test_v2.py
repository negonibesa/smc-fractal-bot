"""
Unified walk-forward test for BNB, RENDER, FIL.
Baseline + after each v2 improvement.
"""
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
    return {'strategy': config['strategy'], 'filters': config.get('filters', {}), 'trailing': config.get('trailing', {})}

def load(symbol, tf='4h_12m'):
    df = pd.read_csv(f'data/raw/{symbol}_{tf}.csv')
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c])
    return df.sort_values('timestamp').reset_index(drop=True)

def run_bt(df_part, params, filters_cfg, trailing_cfg, df_1d=None, filters_flags=None):
    """Run backtest with optional filters (flags dict)."""
    if filters_flags is None:
        filters_flags = {}
    
    center = find_consolidation_center(df_part, lookback=params['lookback'])
    sweep = detect_sweep(df_part, center, threshold=params['sweep_threshold'])
    adx, pdi, mdi = None, None, None
    if filters_cfg.get('adx_filter', {}).get('enabled'):
        adx, pdi, mdi = calculate_adx(df_part, period=14)
    
    # Volume filter: avg volume over 20 periods
    vol_avg = df_part['volume'].rolling(20).mean() if filters_flags.get('volume') else None
    vol_mult = filters_cfg.get('volume_filter', {}).get('volume_multiplier', 1.5)
    
    # 1D trend filter: EMA50
    ema50_1d = None
    if filters_flags.get('trend_1d') and df_1d is not None and len(df_1d) >= 50:
        ema50_1d = df_1d['close'].ewm(span=50).mean().iloc[-1]
        last_1d_close = df_1d['close'].iloc[-1]
        daily_bullish = last_1d_close > ema50_1d
    else:
        daily_bullish = None
    
    signals = []
    state = 0; sw_dir = None; sw_price = None; sw_idx = None; ctr = None; sw_vol = None
    
    for i in range(params['lookback'], len(df_part)):
        c = center.iloc[i] if not pd.isna(center.iloc[i]) else df_part['close'].iloc[i]
        h, l, cl = df_part['high'].iloc[i], df_part['low'].iloc[i], df_part['close'].iloc[i]
        ts = df_part['timestamp'].iloc[i]
        has_bull = sweep['bullish_sweep'].iloc[i] if i < len(sweep['bullish_sweep']) else False
        has_bear = sweep['bearish_sweep'].iloc[i] if i < len(sweep['bearish_sweep']) else False
        
        if state == 0:
            if has_bear: 
                state=1; sw_dir='bearish'; sw_price=h; sw_idx=i; sw_vol = df_part['volume'].iloc[i]
                ctr = c
            elif has_bull: 
                state=1; sw_dir='bullish'; sw_price=l; sw_idx=i; sw_vol = df_part['volume'].iloc[i]
                ctr = c
        
        elif state == 1:
            # For fixed_center: use the saved ctr (already saved at sweep moment)
            # For default: recalculate center at each candle
            if not filters_flags.get('fixed_center'):
                ctr = c  # recalculate every candle (old behavior)
            
            if abs(cl - ctr) / ctr < params['center_proximity']:
                skip = False
                
                # ADX filter
                if adx is not None:
                    cur_adx = adx.iloc[i] if not pd.isna(adx.iloc[i]) else 0
                    if cur_adx < filters_cfg.get('adx_filter', {}).get('min_adx', 25): skip = True
                    if cur_adx > 25:
                        p_val = pdi.iloc[i] if not pd.isna(pdi.iloc[i]) else 0
                        m_val = mdi.iloc[i] if not pd.isna(mdi.iloc[i]) else 0
                        if sw_dir == 'bearish' and m_val < p_val: skip = True
                        if sw_dir == 'bullish' and p_val < m_val: skip = True
                
                # Volume filter: check volume at the SWEEP candle, not entry candle
                if not skip and filters_flags.get('volume') and vol_avg is not None:
                    avg_v = vol_avg.iloc[sw_idx] if not pd.isna(vol_avg.iloc[sw_idx]) else 0
                    if avg_v > 0 and sw_vol is not None:
                        if sw_vol < avg_v * vol_mult:
                            skip = True
                
                # Session filter (UTC hours: London 8-16, NY 13-21)
                if not skip and filters_flags.get('session'):
                    hour = ts.hour if hasattr(ts, 'hour') else 0
                    if not (8 <= hour <= 21):  # outside London+NY
                        skip = True
                
                if skip: state=0; sw_dir=None; continue
                
                entry = ctr
                if sw_dir == 'bearish':
                    stop=sw_price*1.003; risk=abs(entry-stop)
                    tp=entry-risk*params['tp_multiplier']; d='SELL'
                else:
                    stop=sw_price*0.997; risk=abs(stop-entry)
                    tp=entry+risk*params['tp_multiplier']; d='BUY'
                
                # 1D trend filter: skip SELL if daily is bullish, BUY if bearish
                if filters_flags.get('trend_1d') and daily_bullish is not None:
                    if d == 'SELL' and daily_bullish: skip = True
                    if d == 'BUY' and not daily_bullish: skip = True
                    if skip: state=0; sw_dir=None; continue
                
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
        breakeven_at=trailing_cfg.get('breakeven_at',0.5),
        trailing_activate=trailing_cfg.get('trail_activate',1.0),
        trailing_step=trailing_cfg.get('trail_step',0.5))
    return {'pf':metrics['profit_factor'],'wr':metrics['win_rate'],
            'ret':metrics['total_return'],'dd':metrics['max_drawdown'],'trades':metrics['total_trades'],
            'n_signals': len(signals)}


def test_pair(symbol, label, filters_flags, df_1d=None):
    """Test one pair with given filter flags, return results dict."""
    pcfg = get_pair_config(symbol)
    params = pcfg['strategy']
    flt = pcfg.get('filters', {})
    trail = pcfg.get('trailing', config.get('trailing', {}))
    
    df = load(symbol)
    n = len(df)
    s50 = int(n * 0.5)
    s70 = int(n * 0.7)
    
    full = run_bt(df, params, flt, trail, df_1d=df_1d, filters_flags=filters_flags)
    first = run_bt(df.iloc[:s50].copy().reset_index(drop=True), params, flt, trail, df_1d=df_1d, filters_flags=filters_flags)
    second = run_bt(df.iloc[s50:].copy().reset_index(drop=True), params, flt, trail, df_1d=df_1d, filters_flags=filters_flags)
    last = run_bt(df.iloc[s70:].copy().reset_index(drop=True), params, flt, trail, df_1d=df_1d, filters_flags=filters_flags)
    
    return {'full': full, 'first': first, 'second': second, 'last': last, 'n': n}


def print_results(symbol, results, label=""):
    n = results['n']
    full, first, second, last = results['full'], results['first'], results['second'], results['last']
    
    print(f"\n{'='*60}")
    print(f"  {symbol} — {label} ({n} candles)")
    print(f"{'='*60}")
    print(f"  {'Period':<28} {'PF':>6} {'WR':>6} {'Return':>8} {'DD':>6} {'T':>4} {'Sig':>4}")
    print(f"  {'-'*60}")
    for lbl, r in [('FULL', full), ('FIRST 50% (train)', first),
                   ('SECOND 50% (test)', second), ('LAST 30% (recent)', last)]:
        if r:
            print(f"  {lbl:<28} {r['pf']:>6.2f} {r['wr']:>5.0%} {r['ret']:>+7.2%} {r['dd']:>5.2%} {r['trades']:>4} {r['n_signals']:>4}")
    
    oos = [r for r in [second, last] if r]
    is_fit = [r for r in [full, first] if r]
    verdict = "N/A"
    if oos and is_fit:
        avg_oos = np.mean([r['pf'] for r in oos])
        avg_is = np.mean([r['pf'] for r in is_fit])
        avg_oos_ret = np.mean([r['ret'] for r in oos])
        print(f"\n  IS avg PF: {avg_is:.2f} | OOS avg PF: {avg_oos:.2f} | OOS Ret: {avg_oos_ret:+.2%}")
        if avg_oos >= 2.0 and avg_oos_ret > 0:
            verdict = "ROBUST"
        elif avg_oos >= 1.5:
            verdict = "GOOD"
        elif avg_oos >= 1.0:
            verdict = "WEAK"
        else:
            verdict = "OVERFIT"
        print(f"  VERDICT: {verdict}")
        return {'pf_is': avg_is, 'pf_oos': avg_oos, 'ret_oos': avg_oos_ret, 'verdict': verdict}
    return {'verdict': verdict}


if __name__ == '__main__':
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else 'baseline'
    # mode: baseline, volume, fixed_center, trend_1d, session, all, combo
    
    filters_flags = {}
    if mode == 'volume': filters_flags = {'volume': True}
    elif mode == 'fixed_center': filters_flags = {'fixed_center': True}
    elif mode == 'trend_1d': filters_flags = {'trend_1d': True}
    elif mode == 'session': filters_flags = {'session': True}
    elif mode == 'all': filters_flags = {'volume': True, 'fixed_center': True, 'trend_1d': True, 'session': True}
    elif mode == 'combo': filters_flags = {'fixed_center': True, 'session': True}
    elif mode == 'combo+vol': filters_flags = {'fixed_center': True, 'session': True, 'volume': True}
    elif mode == 'combo+trend': filters_flags = {'fixed_center': True, 'session': True, 'trend_1d': True}
    
    print(f"\n{'#'*60}")
    print(f"  MODE: {mode.upper()} | Filters: {filters_flags or 'none (baseline)'}")
    print(f"{'#'*60}")
    
    all_results = {}
    for symbol in ['BNBUSDT', 'RENDERUSDT', 'FILUSDT']:
        df_1d = None
        if filters_flags.get('trend_1d'):
            try:
                df_1d = load(symbol.replace('USDT','') + 'USDT_1d', '1d')
                # Fallback: try the symbol as-is
                if df_1d is None or len(df_1d) < 50:
                    df_1d = load(symbol, '1d')
            except:
                try:
                    df_1d = load(symbol, '1d')
                except:
                    print(f"  WARNING: No 1D data for {symbol}, skipping trend_1d filter")
        
        results = test_pair(symbol, mode, filters_flags, df_1d=df_1d)
        verdict_info = print_results(symbol, results, label=mode.upper())
        all_results[symbol] = verdict_info
    
    # Summary
    print(f"\n{'='*60}")
    print(f"  SUMMARY — {mode.upper()}")
    print(f"{'='*60}")
    for sym, v in all_results.items():
        if v:
            print(f"  {sym:<16} PF_OOS={v.get('pf_oos',0):.2f}  Ret={v.get('ret_oos',0):+.2%}  {v.get('verdict','?')}")
