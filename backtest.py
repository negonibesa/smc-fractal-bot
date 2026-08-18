import numpy as np
import pandas as pd
import logging
from typing import List, Dict, Tuple, Optional
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def run_backtest(
    df: pd.DataFrame,
    signals: List[Dict],
    initial_balance: float = 10000,
    risk_percent: float = 1.0,
    commission: float = 0.001,
    slippage: float = 0.0005,
    stop_buffer: float = 0.002,
    breakeven_at: float = 0.5,    # Move to breakeven after 0.5x risk in profit
    trailing_activate: float = 1.0, # Activate trailing after 1.0x risk in profit
    trailing_step: float = 0.5      # Trail by 0.5x risk
) -> Tuple[List[dict], dict]:
    """
    Бэктест с trailing stop и breakeven:
    - breakeven_at: после Xx риска в прибыли, стоп = вход + commission
    - trailing_activate: после Xx риска в прибыли, начинаем трейлить
    - trailing_step: трейлим на Xx риска от текущей цены
    """
    trades = []
    balance = initial_balance
    position = 0
    entry_price = 0
    stop_price = 0
    tp_price = 0
    entry_time = None
    direction = None
    original_stop = 0
    highest_pnl = 0  # track max profit in risk units
    
    signals_dict = {}
    for s in signals:
        if 'timestamp' in s:
            signals_dict[s['timestamp']] = s
        elif 'index' in s:
            signals_dict[s['index']] = s
    
    for i in range(len(df)):
        current_time = df.index[i] if df.index is not None else i
        current_price = df['close'].iloc[i]
        high = df['high'].iloc[i]
        low = df['low'].iloc[i]
        
        signal = signals_dict.get(current_time) if current_time in signals_dict else None
        if signal is None:
            signal = signals_dict.get(i) if i in signals_dict else None
        
        # ─── Управление позицией ─────────────────────────────────────
        if position != 0:
            risk_unit = abs(entry_price - original_stop)
            
            # Calculate current PnL in risk units
            if direction == 'LONG':
                current_pnl_risk = (high - entry_price) / risk_unit if risk_unit > 0 else 0
            else:
                current_pnl_risk = (entry_price - low) / risk_unit if risk_unit > 0 else 0
            
            highest_pnl = max(highest_pnl, current_pnl_risk)
            
            # Breakeven: move stop to entry after X risk in profit
            if breakeven_at > 0 and highest_pnl >= breakeven_at:
                if direction == 'LONG':
                    new_stop = entry_price + entry_price * commission  # entry + commission
                    if new_stop > stop_price:
                        stop_price = new_stop
                else:
                    new_stop = entry_price - entry_price * commission
                    if new_stop < stop_price:
                        stop_price = new_stop
            
            # Trailing stop: after activation, trail behind price
            if trailing_activate > 0 and trailing_step > 0 and highest_pnl >= trailing_activate:
                trail_distance = risk_unit * trailing_step
                if direction == 'LONG':
                    new_stop = high - trail_distance
                    if new_stop > stop_price:
                        stop_price = new_stop
                else:
                    new_stop = low + trail_distance
                    if new_stop < stop_price:
                        stop_price = new_stop
            
            # Check stop loss (including trailing)
            if direction == 'LONG' and low <= stop_price:
                exit_price = stop_price * (1 - slippage)
                pnl = position * (exit_price - entry_price) - abs(position) * exit_price * commission
                balance += abs(position) * exit_price * (1 - commission)
                
                exit_reason = 'STOP_LOSS'
                if stop_price > original_stop and abs(stop_price - entry_price) < abs(original_stop - entry_price):
                    if abs(stop_price - entry_price) < risk_unit * 0.1:
                        exit_reason = 'BREAKEVEN'
                    else:
                        exit_reason = 'TRAILING_STOP'
                
                trades.append({
                    'entry_time': entry_time, 'exit_time': current_time,
                    'entry_price': entry_price, 'exit_price': exit_price,
                    'pnl': pnl, 'direction': direction, 'exit_reason': exit_reason,
                    'risk_units': highest_pnl
                })
                position = 0
                direction = None
                
            elif direction == 'SHORT' and high >= stop_price:
                exit_price = stop_price * (1 + slippage)
                pnl = position * (entry_price - exit_price) - abs(position) * exit_price * commission
                balance += abs(position) * exit_price * (1 - commission)
                
                exit_reason = 'STOP_LOSS'
                if stop_price < original_stop and abs(stop_price - entry_price) < abs(original_stop - entry_price):
                    if abs(entry_price - stop_price) < risk_unit * 0.1:
                        exit_reason = 'BREAKEVEN'
                    else:
                        exit_reason = 'TRAILING_STOP'
                
                trades.append({
                    'entry_time': entry_time, 'exit_time': current_time,
                    'entry_price': entry_price, 'exit_price': exit_price,
                    'pnl': pnl, 'direction': direction, 'exit_reason': exit_reason,
                    'risk_units': highest_pnl
                })
                position = 0
                direction = None
            
            # Check take profit
            elif direction == 'LONG' and high >= tp_price:
                exit_price = tp_price * (1 - slippage)
                pnl = position * (exit_price - entry_price) - abs(position) * exit_price * commission
                balance += abs(position) * exit_price * (1 - commission)
                trades.append({
                    'entry_time': entry_time, 'exit_time': current_time,
                    'entry_price': entry_price, 'exit_price': exit_price,
                    'pnl': pnl, 'direction': direction, 'exit_reason': 'TAKE_PROFIT',
                    'risk_units': highest_pnl
                })
                position = 0
                direction = None
                
            elif direction == 'SHORT' and low <= tp_price:
                exit_price = tp_price * (1 + slippage)
                pnl = position * (entry_price - exit_price) - abs(position) * exit_price * commission
                balance += abs(position) * exit_price * (1 - commission)
                trades.append({
                    'entry_time': entry_time, 'exit_time': current_time,
                    'entry_price': entry_price, 'exit_price': exit_price,
                    'pnl': pnl, 'direction': direction, 'exit_reason': 'TAKE_PROFIT',
                    'risk_units': highest_pnl
                })
                position = 0
                direction = None
        
        # ─── Entry ───────────────────────────────────────────────────
        if position == 0 and signal is not None:
            signal_type = signal.get('direction') or signal.get('signal')
            if signal_type in ['BUY', 'LONG']:
                entry_price = signal.get('entry', current_price)
                stop_price = signal.get('stop')
                tp_price = signal.get('tp')
                
                if stop_price is None or tp_price is None:
                    continue
                
                stop_buffer_abs = abs(entry_price - stop_price) * stop_buffer
                stop_price = stop_price - stop_buffer_abs
                
                original_stop = stop_price
                highest_pnl = 0
                
                risk_amount = balance * (risk_percent / 100)
                stop_distance = abs(entry_price - stop_price)
                if stop_distance == 0:
                    continue
                position = risk_amount / stop_distance
                
                balance -= position * entry_price * (1 + commission)
                direction = 'LONG'
                entry_time = current_time
                
            elif signal_type in ['SELL', 'SHORT']:
                entry_price = signal.get('entry', current_price)
                stop_price = signal.get('stop')
                tp_price = signal.get('tp')
                
                if stop_price is None or tp_price is None:
                    continue
                
                stop_buffer_abs = abs(entry_price - stop_price) * stop_buffer
                stop_price = stop_price + stop_buffer_abs
                
                original_stop = stop_price
                highest_pnl = 0
                
                risk_amount = balance * (risk_percent / 100)
                stop_distance = abs(entry_price - stop_price)
                if stop_distance == 0:
                    continue
                position = risk_amount / stop_distance
                
                balance -= position * entry_price * (1 + commission)
                direction = 'SHORT'
                entry_time = current_time
        
        # ─── Exit on opposite signal ─────────────────────────────────
        elif position != 0 and signal is not None:
            signal_type = signal.get('direction') or signal.get('signal')
            if (direction == 'LONG' and signal_type in ['SELL', 'SHORT']) or \
               (direction == 'SHORT' and signal_type in ['BUY', 'LONG']):
                exit_price = current_price * (1 - slippage) if direction == 'LONG' else current_price * (1 + slippage)
                pnl = position * (exit_price - entry_price) - abs(position) * exit_price * commission
                balance += abs(position) * exit_price * (1 - commission)
                trades.append({
                    'entry_time': entry_time, 'exit_time': current_time,
                    'entry_price': entry_price, 'exit_price': exit_price,
                    'pnl': pnl, 'direction': direction, 'exit_reason': 'SIGNAL',
                    'risk_units': highest_pnl
                })
                position = 0
                direction = None
    
    # Close last position
    if position != 0:
        final_price = df['close'].iloc[-1]
        exit_price = final_price * (1 - slippage) if direction == 'LONG' else final_price * (1 + slippage)
        pnl = position * (exit_price - entry_price) - abs(position) * exit_price * commission
        balance += abs(position) * exit_price * (1 - commission)
        trades.append({
            'entry_time': entry_time, 'exit_time': df.index[-1] if df.index is not None else len(df) - 1,
            'entry_price': entry_price, 'exit_price': exit_price,
            'pnl': pnl, 'direction': direction, 'exit_reason': 'FORCE_CLOSE',
            'risk_units': highest_pnl
        })
    
    metrics = calculate_backtest_metrics(trades, initial_balance)
    return trades, metrics


def calculate_backtest_metrics(trades: List[dict], initial_balance: float) -> dict:
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0, "profit_factor": 0,
            "max_drawdown": 0, "sharpe_ratio": 0,
            "final_balance": initial_balance, "total_return": 0
        }
    
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(trades) if trades else 0
    total_profit = sum(t["pnl"] for t in wins) if wins else 0
    total_loss = abs(sum(t["pnl"] for t in losses)) if losses else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else float('inf') if total_profit > 0 else 0
    
    balance = initial_balance
    peak_balance = initial_balance
    max_drawdown = 0
    equity = [balance]
    
    for trade in trades:
        balance += trade["pnl"]
        equity.append(balance)
        peak_balance = max(peak_balance, balance)
        drawdown = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0
        max_drawdown = max(max_drawdown, drawdown)
    
    returns = [t["pnl"] / initial_balance for t in trades]
    sharpe_ratio = np.mean(returns) / np.std(returns) * np.sqrt(252) if len(returns) > 1 and np.std(returns) > 0 else 0
    
    final_balance = balance
    total_return = (final_balance - initial_balance) / initial_balance
    
    return {
        "total_trades": len(trades), "win_rate": win_rate,
        "profit_factor": profit_factor, "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe_ratio, "final_balance": final_balance,
        "total_return": total_return
    }


def save_backtest_report(metrics: dict, trades: List[dict], save_path: str):
    with open(save_path, "w") as f:
        f.write("SMC FRACTAL BOT - BACKTEST REPORT\n")
        f.write("=" * 60 + "\n")
        f.write(f"Date: {pd.Timestamp.now()}\n\n")
        f.write("METRICS:\n")
        f.write(f"  Total Trades: {metrics['total_trades']}\n")
        f.write(f"  Win Rate: {metrics['win_rate']:.2%}\n")
        f.write(f"  Profit Factor: {metrics['profit_factor']:.2f}\n")
        f.write(f"  Max Drawdown: {metrics['max_drawdown']:.2%}\n")
        f.write(f"  Sharpe Ratio: {metrics['sharpe_ratio']:.2f}\n")
        f.write(f"  Final Balance: ${metrics['final_balance']:,.2f}\n")
        f.write(f"  Total Return: {metrics['total_return']:.2%}\n\n")
        f.write("TRADE LOG:\n")
        f.write("-" * 60 + "\n")
        for i, trade in enumerate(trades, 1):
            f.write(f"Trade {i}: {trade['direction']} - {trade['exit_reason']}\n")
            f.write(f"  Entry: {trade['entry_time']} @ {trade['entry_price']:.2f}\n")
            f.write(f"  Exit: {trade['exit_time']} @ {trade['exit_price']:.2f}\n")
            f.write(f"  PnL: ${trade['pnl']:.2f}\n\n")
    logger.info(f"Backtest report saved to {save_path}")
