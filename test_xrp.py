import warnings; warnings.filterwarnings('ignore')
from test_v2 import get_pair_config, load, run_bt

symbol = 'XRPUSDT'
pcfg = get_pair_config(symbol)
params = pcfg['strategy']
flt = pcfg.get('filters', {})
trail = pcfg.get('trailing', {})
df = load(symbol)
df_1d = load(symbol, '1d')

full_b = run_bt(df, params, flt, trail, {})
flags = {'fixed_center': True, 'session': True, 'trend_1d': True}
full_o = run_bt(df, params, flt, trail, df_1d=df_1d, filters_flags=flags)

if full_b:
    print(f'Baseline: PF={full_b["pf"]:.2f} T={full_b["trades"]} Ret={full_b["ret"]:+.2%} DD={full_b["dd"]:.2%}')
if full_o:
    print(f'Optimal:  PF={full_o["pf"]:.2f} T={full_o["trades"]} Ret={full_o["ret"]:+.2%} DD={full_o["dd"]:.2%}')
if full_b and full_o:
    print(f'Delta:    PF={full_o["pf"] - full_b["pf"]:+.2f}')
