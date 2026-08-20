import numpy as np
import pandas as pd
import logging
from typing import List, Dict, Tuple, Optional
from pathlib import Path
from datetime import timedelta

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
    breakeven_at: float = 0.5,
    trailing_activate: float = 1.0,
    trailing_step: float = 0.5,
    max_leverage: float = 10.0,
    dynamic_risk: dict = None,
    cooldown_hours: float = 4.0,
    max_daily_loss_pct: float = 5.0,
    max_daily_trades: int = 20,
    max_consecutive_losses: int = 3,
    funding_rates: pd.DataFrame = None,
) -> Tuple[List[dict], dict]:
    """
    Backtest matching live bot behavior:
      - Margin model (balance stays positive)
      - Entry + exit commission
      - Entry + exit slippage
      - Cooldown between trades (default 4H)
      - Circuit breakers: max daily loss, max daily trades
      - Dynamic risk (DD + PF)
    """
    trades = []
    balance = initial_balance
    equity = initial_balance
    peak_equity = initial_balance
    position = 0
    entry_price = 0.0
    stop_price = 0.0
    tp_price = 0.0
    entry_time = None
    direction = None
    original_stop = 0.0
    highest_pnl = 0.0
    margin_used = 0.0

    last_trade_time = None  # for cooldown
    daily_pnl = 0.0
    daily_trades = 0
    current_day = None
    funding_accrued = 0.0
    total_funding = 0.0
    consecutive_losses = 0

    # Dynamic risk params
    dr_base = dynamic_risk.get('base_risk', 1.5) if dynamic_risk else risk_percent
    dr_min = dynamic_risk.get('min_risk', 0.75) if dynamic_risk else risk_percent
    dr_max = dynamic_risk.get('max_risk', 2.0) if dynamic_risk else risk_percent
    dr_dd1 = dynamic_risk.get('dd_threshold_1', 6.0) if dynamic_risk else 999
    dr_dd2 = dynamic_risk.get('dd_threshold_2', 10.0) if dynamic_risk else 999
    dr_pf_hot = dynamic_risk.get('pf_hot', 2.0) if dynamic_risk else 999
    dr_pf_window = dynamic_risk.get('pf_window', 20) if dynamic_risk else 20

    signals_dict = {}
    for s in signals:
        if 'timestamp' in s:
            signals_dict[s['timestamp']] = s
        if 'index' in s:
            signals_dict[s['index']] = s

    def _calc_equity(pos, ep, sp, dir_, current_p):
        if pos == 0:
            return balance
        risk_u = abs(ep - sp)
        if risk_u == 0:
            return balance
        if dir_ == 'LONG':
            unrealized = pos * (current_p - ep)
        else:
            unrealized = pos * (ep - current_p)
        return margin_used + unrealized

    def _close_position(exit_p, reason, current_time):
        nonlocal position, entry_price, stop_price, tp_price, direction
        nonlocal original_stop, highest_pnl, balance, equity, margin_used, peak_equity
        nonlocal daily_pnl, daily_trades, funding_accrued, consecutive_losses

        if direction == 'LONG':
            raw_pnl = position * (exit_p - entry_price)
        else:
            raw_pnl = position * (entry_price - exit_p)

        pnl = raw_pnl - abs(position) * entry_price * commission - abs(position) * exit_p * commission - funding_accrued
        balance += margin_used + raw_pnl - abs(position) * exit_p * commission

        daily_pnl += pnl
        daily_trades += 1

        trades.append({
            'entry_time': entry_time, 'exit_time': current_time,
            'entry_price': entry_price, 'exit_price': exit_p,
            'pnl': pnl, 'direction': direction, 'exit_reason': reason,
            'risk_units': highest_pnl, 'stop_price': original_stop,
        })

        if pnl < 0:
            consecutive_losses += 1
        else:
            consecutive_losses = 0

        position = 0
        funding_accrued = 0.0
        direction = None
        margin_used = 0.0
        equity = balance
        peak_equity = max(peak_equity, balance)

    for i in range(len(df)):
        current_time = df.index[i] if df.index is not None else i
        current_price = df['close'].iloc[i]
        high = df['high'].iloc[i]
        low = df['low'].iloc[i]

        signal = signals_dict.get(current_time) if current_time in signals_dict else None
        if signal is None:
            signal = signals_dict.get(i) if i in signals_dict else None

        # Reset daily counters
        ts = df['timestamp'].iloc[i]
        day = ts.date() if hasattr(ts, 'date') else None
        if day != current_day:
            current_day = day
            daily_pnl = 0.0
            daily_trades = 0

        # ─── Funding rate deduction ────────────────────────────────
        if position != 0 and funding_rates is not None and len(funding_rates) > 0:
            candle_ts = df['timestamp'].iloc[i]
            candle_start = candle_ts
            candle_end = candle_ts + timedelta(hours=4) if hasattr(candle_ts, 'hour') else candle_ts
            # Find funding events within this candle
            mask = (funding_rates['timestamp'] >= candle_start) & (funding_rates['timestamp'] < candle_end)
            fr_events = funding_rates.loc[mask]
            for _, fr_row in fr_events.iterrows():
                rate = fr_row['rate']
                notional = abs(position) * current_price
                funding_cost = notional * rate
                # LONG pays when rate > 0, SHORT pays when rate < 0
                if direction == 'LONG':
                    balance -= funding_cost
                    funding_accrued += funding_cost
                else:
                    balance += funding_cost  # SHORT receives when rate > 0
                    funding_accrued -= funding_cost
                total_funding += abs(funding_cost)
                equity = _calc_equity(position, entry_price, stop_price, direction, current_price)

        # ─── Position management ──────────────────────────────────
        if position != 0:
            risk_unit = abs(entry_price - original_stop)

            if direction == 'LONG':
                current_pnl_risk = (high - entry_price) / risk_unit if risk_unit > 0 else 0
            else:
                current_pnl_risk = (entry_price - low) / risk_unit if risk_unit > 0 else 0

            highest_pnl = max(highest_pnl, current_pnl_risk)

            if breakeven_at > 0 and highest_pnl >= breakeven_at:
                if direction == 'LONG':
                    new_stop = entry_price + entry_price * 0.0005
                    if new_stop > stop_price:
                        stop_price = new_stop
                else:
                    new_stop = entry_price - entry_price * 0.0005
                    if new_stop < stop_price:
                        stop_price = new_stop

            if trailing_activate > 0 and trailing_step > 0 and highest_pnl >= trailing_activate:
                trail_distance = risk_unit * trailing_step
                if direction == 'LONG':
                    new_stop = current_price - trail_distance
                    if new_stop > stop_price:
                        stop_price = new_stop
                else:
                    new_stop = current_price + trail_distance
                    if new_stop < stop_price:
                        stop_price = new_stop

            if direction == 'LONG' and low <= stop_price:
                exit_price = stop_price * (1 - slippage)
                exit_reason = 'STOP_LOSS'
                if stop_price > original_stop and abs(stop_price - entry_price) < abs(original_stop - entry_price):
                    exit_reason = 'BREAKEVEN' if abs(stop_price - entry_price) < risk_unit * 0.1 else 'TRAILING_STOP'
                _close_position(exit_price, exit_reason, current_time)

            elif direction == 'SHORT' and high >= stop_price:
                exit_price = stop_price * (1 + slippage)
                exit_reason = 'STOP_LOSS'
                if stop_price < original_stop and abs(stop_price - entry_price) < abs(original_stop - entry_price):
                    exit_reason = 'BREAKEVEN' if abs(entry_price - stop_price) < risk_unit * 0.1 else 'TRAILING_STOP'
                _close_position(exit_price, exit_reason, current_time)

            elif direction == 'LONG' and high >= tp_price:
                exit_price = tp_price * (1 - slippage)
                _close_position(exit_price, 'TAKE_PROFIT', current_time)

            elif direction == 'SHORT' and low <= tp_price:
                exit_price = tp_price * (1 + slippage)
                _close_position(exit_price, 'TAKE_PROFIT', current_time)

            if position != 0:
                equity = _calc_equity(position, entry_price, stop_price, direction, current_price)
                peak_equity = max(peak_equity, equity)

        # ─── Entry ────────────────────────────────────────────────
        if position == 0 and signal is not None:
            # Circuit breakers
            if daily_trades >= max_daily_trades:
                continue
            if daily_pnl < 0 and equity > 0 and abs(daily_pnl) / equity * 100 >= max_daily_loss_pct:
                continue
            if consecutive_losses >= max_consecutive_losses:
                continue

            # Cooldown
            if last_trade_time is not None:
                ts = df['timestamp'].iloc[i]
                if hasattr(ts, 'timestamp'):
                    elapsed_h = (ts - last_trade_time).total_seconds() / 3600
                    if elapsed_h < cooldown_hours:
                        continue

            current_risk = risk_percent
            if dynamic_risk:
                dd_pct = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0

                current_risk = dr_base

                if dd_pct > dr_dd2:
                    current_risk = dr_min
                elif dd_pct > dr_dd1:
                    current_risk = 1.0

                pf_20 = _calc_pf_last_n(trades, dr_pf_window)
                if pf_20 > dr_pf_hot and dd_pct < 3.0:
                    current_risk = dr_max

                current_risk = max(dr_min, min(current_risk, dr_max))

            signal_type = signal.get('direction') or signal.get('signal')
            if signal_type in ['BUY', 'LONG']:
                entry_price = signal.get('entry', current_price) * (1 + slippage)
                stop_price = signal.get('stop')
                tp_price = signal.get('tp')

                if stop_price is None or tp_price is None:
                    continue

                stop_buffer_abs = abs(entry_price - stop_price) * stop_buffer
                stop_price = stop_price - stop_buffer_abs

                original_stop = stop_price
                highest_pnl = 0

                risk_amount = equity * (current_risk / 100)
                stop_distance = abs(entry_price - stop_price)
                if stop_distance == 0:
                    continue
                position = risk_amount / stop_distance

                notional = position * entry_price
                max_notional = equity * max_leverage
                if notional > max_notional:
                    position = max_notional / entry_price

                margin_used = position * entry_price / max_leverage
                entry_commission = abs(position) * entry_price * commission
                balance -= margin_used + entry_commission
                direction = 'LONG'
                entry_time = current_time
                funding_accrued = 0.0
                last_trade_time = df['timestamp'].iloc[i]
                equity = margin_used

            elif signal_type in ['SELL', 'SHORT']:
                entry_price = signal.get('entry', current_price) * (1 - slippage)
                stop_price = signal.get('stop')
                tp_price = signal.get('tp')

                if stop_price is None or tp_price is None:
                    continue

                stop_buffer_abs = abs(entry_price - stop_price) * stop_buffer
                stop_price = stop_price + stop_buffer_abs

                original_stop = stop_price
                highest_pnl = 0

                risk_amount = equity * (current_risk / 100)
                stop_distance = abs(entry_price - stop_price)
                if stop_distance == 0:
                    continue
                position = risk_amount / stop_distance

                notional = position * entry_price
                max_notional = equity * max_leverage
                if notional > max_notional:
                    position = max_notional / entry_price

                margin_used = position * entry_price / max_leverage
                entry_commission = abs(position) * entry_price * commission
                balance -= margin_used + entry_commission
                direction = 'SHORT'
                entry_time = current_time
                funding_accrued = 0.0
                last_trade_time = df['timestamp'].iloc[i]
                equity = margin_used

        # ─── Exit on opposite signal ─────────────────────────────
        elif position != 0 and signal is not None:
            signal_type = signal.get('direction') or signal.get('signal')
            if (direction == 'LONG' and signal_type in ['SELL', 'SHORT']) or \
               (direction == 'SHORT' and signal_type in ['BUY', 'LONG']):
                if direction == 'LONG':
                    exit_price = current_price * (1 - slippage)
                else:
                    exit_price = current_price * (1 + slippage)
                _close_position(exit_price, 'SIGNAL', current_time)

    if position != 0:
        final_price = df['close'].iloc[-1]
        if direction == 'LONG':
            exit_price = final_price * (1 - slippage)
        else:
            exit_price = final_price * (1 + slippage)
        _close_position(exit_price, 'FORCE_CLOSE',
                        df.index[-1] if df.index is not None else len(df) - 1)

    metrics = calculate_backtest_metrics(trades, initial_balance)
    metrics['total_funding'] = total_funding
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
    equity_curve = [balance]

    for trade in trades:
        balance += trade["pnl"]
        equity_curve.append(balance)
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


def _calc_pf_last_n(trades: List[dict], n: int = 20) -> float:
    if len(trades) < 2:
        return 0
    recent = trades[-n:]
    wins = [t for t in recent if t.get('pnl', 0) > 0]
    losses = [t for t in recent if t.get('pnl', 0) <= 0]
    total_win = sum(t['pnl'] for t in wins) if wins else 0
    total_loss = abs(sum(t['pnl'] for t in losses)) if losses else 0
    if total_loss == 0:
        return float('inf') if total_win > 0 else 0
    return total_win / total_loss
