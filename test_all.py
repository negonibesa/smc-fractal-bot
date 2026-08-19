"""12-month comprehensive backtest for all 3 pairs."""
import warnings; warnings.filterwarnings('ignore')
import yaml, numpy as np, pandas as pd
from pathlib import Path
from backtest import run_backtest
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

def run_bt(df, params, trailing, filters_flags=None, df_1d=None):
    lb = params['lookback']
    if len(df) < lb + 10:
        return None, []

    center = find_consolidation_center(df, lookback=lb)
    sweep = detect_sweep(df, center, threshold=params['sweep_threshold'])
    adx, pdi, mdi = calculate_adx(df, period=14)

    # 1D trend
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

                # Session filter
                if filters_flags and filters_flags.get('session'):
                    ts = df['timestamp'].iloc[i]
                    hour = ts.hour if hasattr(ts, 'hour') else 0
                    if not (8 <= hour <= 21):
                        state=0; sw_dir=None; continue

                # Trend 1D filter
                if filters_flags and filters_flags.get('trend_1d') and daily_ema50 is not None:
                    # Find matching 1D candle
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
                                'direction':d,'entry':entry,'stop':stop,'tp':tp,'confidence':0.7})
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
        breakeven_at=trailing.get('breakeven_at',0.5),
        trailing_activate=trailing.get('trail_activate',1.0),
        trailing_step=trailing.get('trail_step',0.5),
        max_leverage=20,
        dynamic_risk={'default_risk': 1.5, 'reduced_risk': 1.0, 'dd_threshold': 10})
    return metrics, signals

# ─── RUN ALL PAIRS ──────────────────────────────────────────────
print("=" * 80)
print("  12-MONTH BACKTEST — ALL PAIRS")
print("=" * 80)

total_pnl = 0
total_trades = 0
total_wins = 0
total_losses = 0
pair_results = []

for symbol in ['BNBUSDT', 'RENDERUSDT', 'XRPUSDT']:
    pcfg = get_pair_config(symbol)
    if not pcfg:
        print(f"\n{symbol}: NOT IN CONFIG")
        continue

    params = pcfg['strategy']
    trail = pcfg.get('trailing', {})
    filters = pcfg.get('filters', {})
    df = load(symbol, '4h')
    df_1d = load(symbol, '1d')

    if df is None:
        print(f"\n{symbol}: NO DATA")
        continue

    # Determine active filters
    flags = {}
    if filters.get('session_filter', {}).get('enabled'):
        flags['session'] = True
    if filters.get('trend_1d_filter', {}).get('enabled'):
        flags['trend_1d'] = True

    metrics, signals = run_bt(df, params, trail, filters_flags=flags, df_1d=df_1d)

    period_start = df['timestamp'].min().strftime('%Y-%m-%d')
    period_end = df['timestamp'].max().strftime('%Y-%m-%d')
    months = (df['timestamp'].max() - df['timestamp'].min()).days / 30

    if metrics:
        pf = metrics['profit_factor']
        wr = metrics['win_rate']
        ret = metrics['total_return']
        dd = metrics['max_drawdown']
        trades = metrics['total_trades']
        final_bal = metrics['final_balance']
        pnl = final_bal - 10000
        wins = int(trades * wr)
        losses = trades - wins

        total_pnl += pnl
        total_trades += trades
        total_wins += wins
        total_losses += losses

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
        print(f"  PnL:       ${pnl:+,.2f}")
        print(f"  Max DD:    {dd:.2%}")
        print(f"  Avg trade: {ret/trades:+.2%}")

        pair_results.append({
            'symbol': symbol, 'trades': trades, 'wins': wins, 'losses': losses,
            'wr': wr, 'pf': pf, 'ret': ret, 'pnl': pnl, 'dd': dd,
            'period_start': period_start, 'period_end': period_end,
        })
    else:
        print(f"\n{symbol}: no signals generated")

# ─── PORTFOLIO SUMMARY ─────────────────────────────────────────
print(f"\n\n{'=' * 80}")
print(f"  PORTFOLIO SUMMARY")
print(f"{'=' * 80}")

if pair_results:
    avg_pf = np.mean([r['pf'] for r in pair_results])
    avg_wr = np.mean([r['wr'] for r in pair_results])
    max_dd = max(r['dd'] for r in pair_results)

    print(f"\n  {'Pair':<14} {'Trades':>7} {'Wins':>5} {'Losses':>7} {'WR':>6} {'PF':>8} {'Return':>10} {'PnL':>12} {'DD':>8}")
    print(f"  {'─' * 78}")
    for r in pair_results:
        print(f"  {r['symbol']:<14} {r['trades']:>7} {r['wins']:>5} {r['losses']:>7} "
              f"{r['wr']:>5.0%} {r['pf']:>8.2f} {r['ret']:>+9.2%} ${r['pnl']:>+10.2f} {r['dd']:>7.2%}")
    print(f"  {'─' * 78}")
    print(f"  {'TOTAL':<14} {total_trades:>7} {total_wins:>5} {total_losses:>7} "
          f"{total_wins/total_trades:>5.0%} {avg_pf:>8.2f} {'':>10} ${total_pnl:>+10.2f} {max_dd:>7.2%}")

    print(f"\n  Starting capital:  $10,000 per pair = $30,000 total")
    print(f"  Total PnL:         ${total_pnl:+,.2f}")
    print(f"  Total return:      {total_pnl/30000*100:+.2f}%")
    print(f"  Total trades:      {total_trades}")
    print(f"  Avg PF:            {avg_pf:.2f}")
    print(f"  Max DD (single):   {max_dd:.2f}%")

    # Monthly breakdown
    print(f"\n  {'Pair':<14} {'Months':>7} {'PnL':>12} {'$/month':>10}")
    print(f"  {'─' * 45}")
    for r in pair_results:
        months = (pd.Timestamp(r['period_end']) - pd.Timestamp(r['period_start'])).days / 30
        if months > 0:
            pnl_mo = r['pnl'] / months
            print(f"  {r['symbol']:<14} {months:>6.0f}mo ${r['pnl']:>+10.2f} ${pnl_mo:>+8.2f}/mo")
