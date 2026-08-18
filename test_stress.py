"""
Stress test: different timeframes + bear market periods.
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

def load(symbol, tf):
    # Prefer longer files (12m suffix = 2+ years of data)
    for suffix in ['_12m', '']:
        path = f'data/raw/{symbol}_{tf}{suffix}.csv'
        if Path(path).exists():
            df = pd.read_csv(path)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            for c in ['open','high','low','close','volume']:
                df[c] = pd.to_numeric(df[c])
            return df.sort_values('timestamp').reset_index(drop=True)
    return None

def run_bt(df_part, params, trailing):
    lookback = params['lookback']
    if len(df_part) < lookback + 10:
        return None
    
    center = find_consolidation_center(df_part, lookback=lookback)
    sweep = detect_sweep(df_part, center, threshold=params['sweep_threshold'])
    adx, pdi, mdi = calculate_adx(df_part, period=14)
    
    signals = []
    state = 0; sw_dir = None; sw_price = None; sw_idx = None; ctr = None
    
    for i in range(lookback, len(df_part)):
        c = center.iloc[i] if not pd.isna(center.iloc[i]) else df_part['close'].iloc[i]
        h, l, cl = df_part['high'].iloc[i], df_part['low'].iloc[i], df_part['close'].iloc[i]
        has_bull = sweep['bullish_sweep'].iloc[i]
        has_bear = sweep['bearish_sweep'].iloc[i]
        
        if state == 0:
            if has_bear: state=1; sw_dir='bearish'; sw_price=h; sw_idx=i; ctr=c
            elif has_bull: state=1; sw_dir='bullish'; sw_price=l; sw_idx=i; ctr=c
        elif state == 1:
            if abs(cl - ctr) / ctr < params['center_proximity']:
                skip = False
                cur_adx = adx.iloc[i] if not pd.isna(adx.iloc[i]) else 0
                if cur_adx < 25: skip = True
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
    bt_df = df_part.iloc[lookback:].copy()
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


def test_timeframe(symbol, tf, label):
    """Test strategy on different timeframe."""
    df = load(symbol, tf)
    if df is None or len(df) < 50:
        return None
    
    pcfg = get_pair_config(symbol)
    params = pcfg['strategy']
    trail = pcfg.get('trailing', {})
    
    # Full period
    full = run_bt(df, params, trail)
    
    # Split into periods
    n = len(df)
    s33 = int(n * 0.33)
    s66 = int(n * 0.66)
    
    first = run_bt(df.iloc[:s33].copy().reset_index(drop=True), params, trail)
    mid = run_bt(df.iloc[s33:s66].copy().reset_index(drop=True), params, trail)
    last = run_bt(df.iloc[s66:].copy().reset_index(drop=True), params, trail)
    
    return {'full': full, 'first': first, 'mid': mid, 'last': last, 'n': n, 'label': label}


def test_bear_period(symbol, tf='4h'):
    """Test specifically on bear market periods."""
    df = load(symbol, tf)
    if df is None or len(df) < 100:
        return None
    
    pcfg = get_pair_config(symbol)
    params = pcfg['strategy']
    trail = pcfg.get('trailing', {})
    
    # Find bear periods: price drops > 15% from peak
    close = df['close'].values
    peak = np.maximum.accumulate(close)
    drawdown = (peak - close) / peak
    
    # Identify bear segments (>10% drawdown from peak)
    bear_mask = drawdown > 0.10
    
    results = []
    in_bear = False
    start = 0
    
    for i in range(len(df)):
        if bear_mask[i] and not in_bear:
            start = i
            in_bear = True
        elif not bear_mask[i] and in_bear:
            if i - start > 10:  # at least 10 candles
                segment = df.iloc[start:i].copy().reset_index(drop=True)
                result = run_bt(segment, params, trail)
                if result:
                    period_start = df['timestamp'].iloc[start]
                    period_end = df['timestamp'].iloc[i-1]
                    dd = drawdown[start:i].max()
                    results.append({
                        'start': period_start, 'end': period_end,
                        'dd': dd, 'result': result
                    })
            in_bear = False
    
    return results


def print_results(results, symbol, test_type):
    if not results:
        print(f"  {symbol} {test_type}: NO DATA")
        return
    
    print(f"\n{'='*65}")
    print(f"  {symbol} — {test_type}")
    print(f"{'='*65}")
    
    if isinstance(results, dict):
        # Timeframe test
        for lbl in ['full', 'first', 'mid', 'last']:
            r = results[lbl]
            label = f"{results['label']} {lbl}"
            if r:
                print(f"  {label:<30} PF={r['pf']:>6.2f} WR={r['wr']:>5.0%} "
                      f"Ret={r['ret']:>+7.2%} DD={r['dd']:>5.2%} T={r['trades']:>3}")
            else:
                print(f"  {label:<30} {'N/A':>6}")
    elif isinstance(results, list):
        # Bear period test
        print(f"  {'Period':<25} {'DD':>6} {'PF':>6} {'WR':>6} {'Return':>8} {'T':>4}")
        print(f"  {'-'*55}")
        for r in results:
            res = r['result']
            print(f"  {r['start'].strftime('%Y-%m-%d')}-{r['end'].strftime('%m-%d'):<12} "
                  f"{r['dd']:>5.1%} {res['pf']:>6.2f} {res['wr']:>5.0%} "
                  f"{res['ret']:>+7.2%} {res['trades']:>4}")
        
        avg_pf = np.mean([r['result']['pf'] for r in results])
        avg_ret = np.mean([r['result']['ret'] for r in results])
        print(f"\n  Bear periods avg: PF={avg_pf:.2f}, Ret={avg_ret:+.2%} ({len(results)} periods)")


if __name__ == '__main__':
    print("=" * 65)
    print("  STRESS TEST: TIMEFRAMES + BEAR PERIODS")
    print("=" * 65)
    
    for symbol in ['BNBUSDT', 'RENDERUSDT']:
        # --- TIMEFRAME TEST ---
        print(f"\n{'#'*65}")
        print(f"  TIMEFRAME TEST: {symbol}")
        print(f"{'#'*65}")
        
        for tf, label in [('1h', '1H'), ('4h', '4H'), ('1d', '1D')]:
            results = test_timeframe(symbol, tf, label)
            print_results(results, symbol, f"TF={label}")
        
        # --- BEAR PERIOD TEST ---
        print(f"\n{'#'*65}")
        print(f"  BEAR PERIOD TEST: {symbol}")
        print(f"{'#'*65}")
        
        bear_results = test_bear_period(symbol, '4h')
        print_results(bear_results, symbol, "BEAR 4H")
    
    # --- SUMMARY ---
    print(f"\n{'='*65}")
    print(f"  STRESS TEST SUMMARY")
    print(f"{'='*65}")
    
    for symbol in ['BNBUSDT', 'RENDERUSDT']:
        print(f"\n  {symbol}:")
        for tf, label in [('1h', '1H'), ('4h', '4H'), ('1d', '1D')]:
            r = test_timeframe(symbol, tf, label)
            if r and r['full']:
                print(f"    {label}: PF={r['full']['pf']:.2f} WR={r['full']['wr']:.0%} "
                      f"Ret={r['full']['ret']:+.2%} DD={r['full']['dd']:.2%} "
                      f"T={r['full']['trades']} ({r['n']} candles)")
        
        bears = test_bear_period(symbol, '4h')
        if bears:
            avg_pf = np.mean([r['result']['pf'] for r in bears])
            avg_ret = np.mean([r['result']['ret'] for r in bears])
            print(f"    Bear avg: PF={avg_pf:.2f} Ret={avg_ret:+.2%} ({len(bears)} bear periods)")
