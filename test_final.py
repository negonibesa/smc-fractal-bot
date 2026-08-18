"""Final comparison: baseline vs optimal per-pair."""
import warnings; warnings.filterwarnings('ignore')
import numpy as np
from test_v2 import get_pair_config, load, run_bt

configs = {
    'BNBUSDT': {'fixed_center': True, 'session': True, 'trend_1d': True},
    'RENDERUSDT': {'fixed_center': True, 'session': True},
    'FILUSDT': {'fixed_center': True, 'trend_1d': True},
}

def run_full(df, params, flt, trail, flags, df_1d=None):
    n = len(df)
    s50 = int(n * 0.5)
    s70 = int(n * 0.7)
    full = run_bt(df, params, flt, trail, df_1d=df_1d, filters_flags=flags)
    first = run_bt(df.iloc[:s50].copy().reset_index(drop=True), params, flt, trail, df_1d=df_1d, filters_flags=flags)
    second = run_bt(df.iloc[s50:].copy().reset_index(drop=True), params, flt, trail, df_1d=df_1d, filters_flags=flags)
    last = run_bt(df.iloc[s70:].copy().reset_index(drop=True), params, flt, trail, df_1d=df_1d, filters_flags=flags)
    return full, first, second, last

print("=" * 70)
print("  FINAL COMPARISON: BASELINE vs OPTIMAL PER-PAIR")
print("=" * 70)
print(f"  {'Pair':<16} {'Base PF':>8} {'Opt PF':>8} {'Base Ret':>10} {'Opt Ret':>10} {'Delta':>8}")
print(f"  {'-'*62}")

for symbol in ['BNBUSDT', 'RENDERUSDT', 'FILUSDT']:
    pcfg = get_pair_config(symbol)
    params = pcfg['strategy']
    flt = pcfg.get('filters', {})
    trail = pcfg.get('trailing', {})
    
    df = load(symbol)
    df_1d = load(symbol, '1d')
    
    # Baseline
    full_b, first_b, second_b, last_b = run_full(df, params, flt, trail, {})
    # Optimal
    flags = configs[symbol]
    full_o, first_o, second_o, last_o = run_full(df, params, flt, trail, flags, df_1d=df_1d)
    
    # OOS metrics
    base_oos_pf = np.mean([r['pf'] for r in [second_b, last_b] if r])
    opt_oos_pf = np.mean([r['pf'] for r in [second_o, last_o] if r])
    base_oos_ret = np.mean([r['ret'] for r in [second_b, last_b] if r])
    opt_oos_ret = np.mean([r['ret'] for r in [second_o, last_o] if r])
    delta = opt_oos_pf - base_oos_pf
    
    print(f"  {symbol:<16} {base_oos_pf:>8.2f} {opt_oos_pf:>8.2f} {base_oos_ret:>+9.2%} {opt_oos_ret:>+9.2%} {delta:>+7.2f}")

print()
print("  Filters used per pair:")
for sym, f in configs.items():
    print(f"    {sym}: {f}")
