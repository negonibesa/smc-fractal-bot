import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from pathlib import Path
from data_loader import BybitLoader
from backtest import run_backtest
from smc_features import find_consolidation_center, detect_sweep


def generate_signals(df_4h, lookback, sweep_thresh, center_prox, tp_mult, timeout):
    center = find_consolidation_center(df_4h, lookback=lookback)
    sweep = detect_sweep(df_4h, center, threshold=sweep_thresh)
    
    signals_list = []
    state = 0
    sweep_direction = None
    sweep_price = None
    sweep_index = None
    center_at_sweep = None
    
    for i in range(lookback, len(df_4h)):
        current_close = df_4h['close'].iloc[i]
        current_high = df_4h['high'].iloc[i]
        current_low = df_4h['low'].iloc[i]
        c = center.iloc[i] if not pd.isna(center.iloc[i]) else current_close
        
        has_bullish = sweep['bullish_sweep'].iloc[i] if i < len(sweep['bullish_sweep']) else False
        has_bearish = sweep['bearish_sweep'].iloc[i] if i < len(sweep['bearish_sweep']) else False
        
        if state == 0:
            if has_bearish:
                state = 1
                sweep_direction = 'bearish'
                sweep_price = current_high
                sweep_index = i
                center_at_sweep = c
            elif has_bullish:
                state = 1
                sweep_direction = 'bullish'
                sweep_price = current_low
                sweep_index = i
                center_at_sweep = c
        elif state == 1:
            if abs(current_close - center_at_sweep) / center_at_sweep < center_prox:
                entry = center_at_sweep
                if sweep_direction == 'bearish':
                    stop = sweep_price * 1.003
                    risk = abs(entry - stop)
                    tp = entry - risk * tp_mult
                    direction = 'SELL'
                else:
                    stop = sweep_price * 0.997
                    risk = abs(stop - entry)
                    tp = entry + risk * tp_mult
                    direction = 'BUY'
                
                signals_list.append({
                    'index': i, 'timestamp': str(df_4h['timestamp'].iloc[i]),
                    'direction': direction, 'entry': entry,
                    'stop': stop, 'tp': tp, 'confidence': 0.7
                })
                state = 0
                sweep_direction = None
            elif i - sweep_index > timeout:
                state = 0
                sweep_direction = None
    
    return signals_list


def plot_trades(symbol, df_4h, trades, metrics, params, save_dir='logs'):
    fig, axes = plt.subplots(3, 1, figsize=(20, 14), gridspec_kw={'height_ratios': [3, 1, 1]})
    fig.suptitle(f'{symbol} — SMC Structure Trading\n'
                 f'PF={metrics["profit_factor"]:.2f} | WR={metrics["win_rate"]:.0%} | '
                 f'Return={metrics["total_return"]:+.2%} | Trades={metrics["total_trades"]}',
                 fontsize=14, fontweight='bold')
    
    # Plot 1: Price + Trades
    ax1 = axes[0]
    timestamps = df_4h['timestamp'].values
    closes = df_4h['close'].values
    
    ax1.plot(timestamps, closes, color='#333333', linewidth=0.8, alpha=0.8, label='Price')
    
    # Mark entries and exits
    for t in trades:
        entry_time = pd.to_datetime(t['entry_time'])
        exit_time = pd.to_datetime(t['exit_time'])
        
        color = '#2ecc71' if t['pnl'] > 0 else '#e74c3c'
        marker_entry = '^' if t['direction'] == 'LONG' else 'v'
        marker_exit = 'x'
        
        # Entry marker
        ax1.scatter(entry_time, t['entry_price'], color=color, marker=marker_entry, 
                   s=100, zorder=5, edgecolors='black', linewidth=0.5)
        
        # Exit marker
        ax1.scatter(exit_time, t['exit_price'], color=color, marker=marker_exit,
                   s=80, zorder=5, edgecolors='black', linewidth=0.5)
        
        # Line between entry and exit
        ax1.plot([entry_time, exit_time], [t['entry_price'], t['exit_price']], 
                color=color, linewidth=1.5, alpha=0.6)
        
        # PnL label
        mid_time = entry_time + (exit_time - entry_time) / 2
        mid_price = (t['entry_price'] + t['exit_price']) / 2
        pnl_label = f"${t['pnl']:+.0f}"
        ax1.annotate(pnl_label, (mid_time, mid_price), fontsize=7,
                    ha='center', va='bottom', color=color, fontweight='bold')
    
    ax1.set_ylabel('Price', fontsize=11)
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    
    # Plot 2: Equity curve
    ax2 = axes[1]
    equity = [10000]
    for t in trades:
        equity.append(equity[-1] + t['pnl'])
    
    equity_times = [pd.to_datetime(trades[0]['entry_time']) if trades else timestamps[0]]
    for t in trades:
        equity_times.append(pd.to_datetime(t['exit_time']))
    
    ax2.fill_between(equity_times, equity, 10000, 
                     where=[e >= 10000 for e in equity], color='#2ecc71', alpha=0.3)
    ax2.fill_between(equity_times, equity, 10000,
                     where=[e < 10000 for e in equity], color='#e74c3c', alpha=0.3)
    ax2.plot(equity_times, equity, color='#333333', linewidth=1.5)
    ax2.axhline(y=10000, color='gray', linestyle='--', alpha=0.5)
    ax2.set_ylabel('Equity ($)', fontsize=11)
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    
    # Plot 3: PnL per trade
    ax3 = axes[2]
    pnls = [t['pnl'] for t in trades]
    colors = ['#2ecc71' if p > 0 else '#e74c3c' for p in pnls]
    ax3.bar(range(len(pnls)), pnls, color=colors, alpha=0.7)
    ax3.axhline(y=0, color='gray', linestyle='-', alpha=0.5)
    ax3.set_ylabel('PnL ($)', fontsize=11)
    ax3.set_xlabel('Trade #', fontsize=11)
    ax3.grid(True, alpha=0.3)
    
    plt.tight_layout()
    
    save_path = Path(save_dir) / f'{symbol}_trades_visual.png'
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Chart saved: {save_path}")
    return save_path


# Run for all pairs
loader = BybitLoader()
configs = {
    'BTCUSDT': {'lookback': 15, 'sweep_thresh': 0.012, 'center_prox': 0.005, 'tp_mult': 2.0, 'timeout': 6},
    'SOLUSDT': {'lookback': 15, 'sweep_thresh': 0.010, 'center_prox': 0.003, 'tp_mult': 2.0, 'timeout': 6},
    'BNBUSDT': {'lookback': 12, 'sweep_thresh': 0.010, 'center_prox': 0.008, 'tp_mult': 2.0, 'timeout': 10},
}

for symbol, params in configs.items():
    print(f"\n{'='*60}")
    print(f"  {symbol} — VISUALIZATION")
    print(f"{'='*60}")
    
    df_4h = loader.load_from_csv(f'{symbol}_4h.csv')
    signals = generate_signals(df_4h, **params)
    
    bt_df = df_4h.iloc[params['lookback']:].copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    signals_dict = {s['timestamp']: s for s in signals}
    bt_signals = [signals_dict[ts] for ts in bt_df.index if ts in signals_dict]
    
    trades, metrics = run_backtest(bt_df, bt_signals, initial_balance=10000, risk_percent=1.0,
                                    breakeven_at=0.5, trailing_activate=1.0, trailing_step=0.5)
    
    print(f"  Trades: {metrics['total_trades']} | WR: {metrics['win_rate']:.0%} | PF: {metrics['profit_factor']:.2f}")
    print(f"  Return: {metrics['total_return']:+.2%} | DD: {metrics['max_drawdown']:.2%}")
    
    # Exit reason breakdown
    reasons = {}
    for t in trades:
        r = t['exit_reason']
        reasons[r] = reasons.get(r, 0) + 1
    print(f"  Exits: {reasons}")
    
    plot_trades(symbol, df_4h, trades, metrics, params)
