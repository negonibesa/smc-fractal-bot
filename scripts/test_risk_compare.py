"""Backtest at 1.5% risk."""
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

def run_bt(df, params, trailing, filters_flags=None, df_1d=None, risk_pct=1.0):
    lb = params['lookback']
    if len(df) < lb + 10:
        return None

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
        return None

    bt_df = df.iloc[lb:].copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]
    if not bs:
        return None

    _, metrics = run_backtest(bt_df, bs, initial_balance=10000, risk_percent=risk_pct,
        breakeven_at=trailing.get('breakeven_at',0.5),
        trailing_activate=trailing.get('trail_activate',1.0),
        trailing_step=trailing.get('trail_step',0.5),
        max_leverage=20)
    return metrics

# Compare 1% vs 1.5%
for risk in [1.0, 1.5]:
    print(f"\n{'='*70}")
    print(f"  RISK = {risk}%")
    print(f"{'='*70}")
    total_pnl = 0
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

        m = run_bt(df, params, trail, flags, df_1d, risk_pct=risk)
        if m:
            pnl = m['final_balance'] - 10000
            total_pnl += pnl
            print(f"  {symbol:<14} PF={m['profit_factor']:.2f}  WR={m['win_rate']:.0%}  "
                  f"Return={m['total_return']:+.2%}  PnL=${pnl:+,.2f}  DD={m['max_drawdown']:.2%}  Trades={m['total_trades']}")
    print(f"  {'TOTAL':<14} {'':>8} {'':>6} {'':>10} PnL=${total_pnl:+,.2f}")
