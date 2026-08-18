import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from data_loader import BybitLoader
from backtest import run_backtest, save_backtest_report
from smc_features import find_consolidation_center, detect_sweep, calculate_adx


def generate_signals(df_4h, params, adx_enabled=False, adx_min=25):
    center = find_consolidation_center(df_4h, lookback=params['lookback'])
    sweep = detect_sweep(df_4h, center, threshold=params['sweep_threshold'])
    
    adx, plus_di, minus_di = None, None, None
    if adx_enabled:
        adx, plus_di, minus_di = calculate_adx(df_4h, period=14)
    
    signals_list = []
    state = 0
    sweep_direction = None
    sweep_price = None
    sweep_index = None
    center_at_sweep = None
    
    for i in range(params['lookback'], len(df_4h)):
        current_close = df_4h['close'].iloc[i]
        current_high = df_4h['high'].iloc[i]
        current_low = df_4h['low'].iloc[i]
        c = center.iloc[i] if not pd.isna(center.iloc[i]) else current_close
        
        has_bullish = sweep['bullish_sweep'].iloc[i] if i < len(sweep['bullish_sweep']) else False
        has_bearish = sweep['bearish_sweep'].iloc[i] if i < len(sweep['bearish_sweep']) else False
        
        if state == 0:
            if has_bearish:
                state = 1; sweep_direction = 'bearish'; sweep_price = current_high
                sweep_index = i; center_at_sweep = c
            elif has_bullish:
                state = 1; sweep_direction = 'bullish'; sweep_price = current_low
                sweep_index = i; center_at_sweep = c
        elif state == 1:
            if abs(current_close - center_at_sweep) / center_at_sweep < params['center_proximity']:
                skip = False
                
                if adx_enabled and adx is not None:
                    current_adx = adx.iloc[i] if not pd.isna(adx.iloc[i]) else 0
                    if current_adx < adx_min:
                        skip = True
                    if current_adx > 25:
                        current_plus = plus_di.iloc[i] if not pd.isna(plus_di.iloc[i]) else 0
                        current_minus = minus_di.iloc[i] if not pd.isna(minus_di.iloc[i]) else 0
                        if sweep_direction == 'bearish' and current_minus < current_plus:
                            skip = True
                        if sweep_direction == 'bullish' and current_plus < current_minus:
                            skip = True
                
                if skip:
                    state = 0; sweep_direction = None; continue
                
                entry = center_at_sweep
                if sweep_direction == 'bearish':
                    stop = sweep_price * 1.003; risk = abs(entry - stop)
                    tp = entry - risk * params['tp_multiplier']; direction = 'SELL'
                else:
                    stop = sweep_price * 0.997; risk = abs(stop - entry)
                    tp = entry + risk * params['tp_multiplier']; direction = 'BUY'
                
                signals_list.append({
                    'index': i, 'timestamp': str(df_4h['timestamp'].iloc[i]),
                    'direction': direction, 'entry': entry,
                    'stop': stop, 'tp': tp, 'confidence': 0.7
                })
                state = 0; sweep_direction = None
            elif i - sweep_index > params['timeout']:
                state = 0; sweep_direction = None
    
    return signals_list


loader = BybitLoader()
df_4h = loader.load_from_csv('BTCUSDT_4h_12m.csv')

# Last 12 months
df_4h = df_4h.tail(2190).reset_index(drop=True)

params = {
    'lookback': 12,
    'sweep_threshold': 0.008,
    'center_proximity': 0.012,
    'tp_multiplier': 1.0,
    'timeout': 15
}

print(f"{'='*60}")
print(f"  BTCUSDT — BACKTEST (Best Config)")
print(f"{'='*60}")
print(f"  Data: {len(df_4h)} 4H candles (12 months)")
print(f"  Config: LB={params['lookback']} SW={params['sweep_threshold']:.3f} CP={params['center_proximity']:.3f} TP={params['tp_multiplier']:.1f}x TO={params['timeout']}")
print(f"  Trailing: BE=0.5x, Trail=1.0x, Step=0.5x")
print(f"  ADX Filter: ON (min=25)")

signals = generate_signals(df_4h, params, adx_enabled=True, adx_min=25)

buy_count = sum(1 for s in signals if s['direction'] == 'BUY')
sell_count = sum(1 for s in signals if s['direction'] == 'SELL')
print(f"\n  Signals: BUY={buy_count} SELL={sell_count} Total={len(signals)}")

bt_df = df_4h.iloc[params['lookback']:].copy()
bt_df.index = [str(t) for t in bt_df['timestamp']]
sd = {s['timestamp']: s for s in signals}
bs = [sd[ts] for ts in bt_df.index if ts in sd]

trades, metrics = run_backtest(bt_df, bs, initial_balance=10000, risk_percent=1.0,
                                breakeven_at=0.5, trailing_activate=1.0, trailing_step=0.5)

pf = metrics['profit_factor']
ret = metrics['total_return']
wr = metrics['win_rate']
dd = metrics['max_drawdown']
n = metrics['total_trades']
bal = metrics['final_balance']

reasons = {}
for t in trades:
    reasons[t['exit_reason']] = reasons.get(t['exit_reason'], 0) + 1

print(f"\n  --- RESULTS ---")
print(f"  Trades: {n}")
print(f"  Win Rate: {wr:.0%}")
print(f"  Profit Factor: {pf:.2f}")
print(f"  Return: {ret:+.2%}")
print(f"  Max Drawdown: {dd:.2%}")
print(f"  Final Balance: ${bal:,.2f}")
print(f"  PnL: ${bal - 10000:+,.2f}")
print(f"\n  Exits: {reasons}")

print(f"\n  --- TRADE LOG ---")
print(f"  {'#':>3} | {'Dir':>4} | {'Exit':>12} | {'Entry':>10} | {'Exit':>10} | {'PnL':>10}")
print(f"  {'-'*68}")
for idx, t in enumerate(trades):
    pnl_str = f"$ {t['pnl']:>+8.2f}"
    print(f"  {idx+1:>3} | {t['direction']:>4} | {t['exit_reason']:>12} | {t['entry_price']:>10.2f} | {t['exit_price']:>10.2f} | {pnl_str}")

save_backtest_report(metrics, trades, 'logs/backtest_BTC_best_config_report.txt')
print(f"\n  Report saved to logs/backtest_BTC_best_config_report.txt")
