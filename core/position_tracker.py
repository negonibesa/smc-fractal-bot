"""
Position Tracker —отслеживание открытых позиций и trailing stop
"""

import time
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass, field
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Открытая позиция."""
    symbol: str
    side: str  # LONG / SHORT
    entry_price: float
    size: float
    stop_price: float
    tp_price: float
    entry_time: float
    highest_pnl_risk: float = 0.0
    original_stop: float = 0.0
    trailing_active: bool = False
    
    @property
    def risk_unit(self) -> float:
        return abs(self.entry_price - self.original_stop)
    
    def unrealized_pnl(self, current_price: float) -> float:
        """Нереализованный PnL."""
        if self.side == "LONG":
            return self.size * (current_price - self.entry_price)
        else:
            return self.size * (self.entry_price - current_price)
    
    def pnl_in_risk(self, current_price: float) -> float:
        """PnL в единицах риска."""
        if self.risk_unit == 0:
            return 0
        if self.side == "LONG":
            return (current_price - self.entry_price) / self.risk_unit
        else:
            return (self.entry_price - current_price) / self.risk_unit


class PositionTracker:
    """Отслеживание позиций и управление trailing stop."""
    
    def __init__(self, trailing_enabled: bool = True,
                 breakeven_at: float = 0.5,
                 trail_activate: float = 1.0,
                 trail_step: float = 0.5,
                 redis_store=None):
        """
        Args:
            trailing_enabled: Включить trailing stop
            breakeven_at: После Xx риска → move to breakeven
            trail_activate: После Xx риска → начать трейлить
            trail_step: Трейлить на Xx риска от цены
            redis_store: RedisStore instance for persistence
        """
        self.trailing_enabled = trailing_enabled
        self.breakeven_at = breakeven_at
        self.trail_activate = trail_activate
        self.trail_step = trail_step
        self.redis = redis_store
        
        self.positions: Dict[str, Position] = {}  # symbol → Position
        self.closed_trades: deque = deque(maxlen=500)
        self.trade_log: List[Dict] = []
        
        # Restore from Redis on startup
        if self.redis:
            self._restore_from_redis()

    def _restore_from_redis(self):
        """Load positions and trades from Redis."""
        try:
            # Positions
            saved = self.redis.load_all_positions()
            for sym, p in saved.items():
                pos = Position(
                    symbol=p['symbol'],
                    side=p['side'],
                    entry_price=p['entry_price'],
                    size=p['size'],
                    stop_price=p['stop_price'],
                    tp_price=p['tp_price'],
                    entry_time=p['entry_time'],
                    highest_pnl_risk=p.get('highest_pnl_risk', 0.0),
                    original_stop=p.get('original_stop', p['stop_price']),
                    trailing_active=p.get('trailing_active', False),
                )
                self.positions[sym] = pos
                logger.info(f"RESTORE POSITION: {pos.side} {pos.size} {sym} @ {pos.entry_price}")

            # Closed trades
            saved_trades = self.redis.load_closed_trades()
            for t in saved_trades:
                self.closed_trades.append(t)
            if saved_trades:
                logger.info(f"RESTORE TRADES: {len(saved_trades)} closed trades loaded")

        except Exception as e:
            logger.error(f"Redis restore failed: {e}")

    def _save_position(self, symbol: str, pos: Position):
        """Persist position to Redis."""
        if not self.redis:
            return
        try:
            self.redis.save_position(symbol, {
                'symbol': pos.symbol,
                'side': pos.side,
                'entry_price': pos.entry_price,
                'size': pos.size,
                'stop_price': pos.stop_price,
                'tp_price': pos.tp_price,
                'entry_time': pos.entry_time,
                'highest_pnl_risk': pos.highest_pnl_risk,
                'original_stop': pos.original_stop,
                'trailing_active': pos.trailing_active,
            })
        except Exception as e:
            logger.error(f"Redis save position failed: {e}")

    def _delete_position(self, symbol: str):
        """Remove position from Redis."""
        if not self.redis:
            return
        try:
            self.redis.delete_position(symbol)
        except Exception as e:
            logger.error(f"Redis delete position failed: {e}")

    def _save_trade(self, trade: dict):
        """Persist closed trade to Redis."""
        if not self.redis:
            return
        try:
            self.redis.save_closed_trade(trade)
        except Exception as e:
            logger.error(f"Redis save trade failed: {e}")
    
    def open_position(self, symbol: str, side: str, entry: float,
                      size: float, stop: float, tp: float) -> Position:
        """Зарегистрировать открытие позиции."""
        pos = Position(
            symbol=symbol,
            side=side,
            entry_price=entry,
            size=size,
            stop_price=stop,
            tp_price=tp,
            entry_time=time.time(),
            original_stop=stop,
        )
        self.positions[symbol] = pos
        self._save_position(symbol, pos)
        logger.info(f"TRACK OPEN: {side} {size} {symbol} @ {entry} SL={stop} TP={tp}")
        return pos
    
    def update_trailing(self, symbol: str, current_price: float,
                        high: float = None, low: float = None) -> Optional[float]:
        """
        Обновить trailing stop для позиции.
        
        Returns:
            Новый stop price если изменился, иначе None
        """
        pos = self.positions.get(symbol)
        if not pos or not self.trailing_enabled:
            return None
        
        # Используем high/low для точного расчёта
        price = current_price
        if pos.side == "LONG" and high is not None:
            price = high
        elif pos.side == "SHORT" and low is not None:
            price = low
        
        risk_unit = pos.risk_unit
        if risk_unit == 0:
            return None
        
        # PnL в единицах риска
        pnl_risk = pos.pnl_in_risk(price)
        pos.highest_pnl_risk = max(pos.highest_pnl_risk, pnl_risk)
        
        old_stop = pos.stop_price
        new_stop = old_stop
        
        # Breakeven
        if self.breakeven_at > 0 and pos.highest_pnl_risk >= self.breakeven_at:
            if pos.side == "LONG":
                be_stop = pos.entry_price + pos.entry_price * 0.0005  # commission
                new_stop = max(new_stop, be_stop)
            else:
                be_stop = pos.entry_price - pos.entry_price * 0.0005
                new_stop = min(new_stop, be_stop)
        
        # Trailing
        if self.trail_activate > 0 and self.trail_step > 0:
            if pos.highest_pnl_risk >= self.trail_activate:
                pos.trailing_active = True
                trail_dist = risk_unit * self.trail_step
                
                if pos.side == "LONG":
                    trail_stop = current_price - trail_dist
                    new_stop = max(new_stop, trail_stop)
                else:
                    trail_stop = current_price + trail_dist
                    new_stop = min(new_stop, trail_stop)
        
        if new_stop != old_stop:
            pos.stop_price = new_stop
            self._save_position(symbol, pos)
            logger.info(f"TRAIL {symbol}: SL {old_stop:.2f} → {new_stop:.2f} "
                       f"(pnl_risk={pos.highest_pnl_risk:.2f})")
            return new_stop
        
        return None
    
    def check_exits(self, symbol: str, high: float, low: float,
                    close: float) -> Optional[Dict]:
        """
        Проверить срабатывание SL/TP.
        
        Returns:
            dict с результатом если позиция закрыта, иначе None
        """
        pos = self.positions.get(symbol)
        if not pos:
            return None
        
        exit_price = None
        exit_reason = None
        
        if pos.side == "LONG":
            if low <= pos.stop_price:
                exit_price = pos.stop_price
                exit_reason = "STOP_LOSS"
                if pos.trailing_active:
                    exit_reason = "TRAILING_STOP"
                elif pos.stop_price > pos.original_stop and \
                     abs(pos.stop_price - pos.entry_price) < pos.risk_unit * 0.1:
                    exit_reason = "BREAKEVEN"
            elif high >= pos.tp_price:
                exit_price = pos.tp_price
                exit_reason = "TAKE_PROFIT"
        else:
            if high >= pos.stop_price:
                exit_price = pos.stop_price
                exit_reason = "STOP_LOSS"
                if pos.trailing_active:
                    exit_reason = "TRAILING_STOP"
                elif pos.stop_price < pos.original_stop and \
                     abs(pos.entry_price - pos.stop_price) < pos.risk_unit * 0.1:
                    exit_reason = "BREAKEVEN"
            elif low <= pos.tp_price:
                exit_price = pos.tp_price
                exit_reason = "TAKE_PROFIT"
        
        if exit_price is not None:
            pnl = pos.unrealized_pnl(exit_price)
            result = {
                'symbol': symbol,
                'side': pos.side,
                'entry_price': pos.entry_price,
                'exit_price': exit_price,
                'size': pos.size,
                'pnl': pnl,
                'exit_reason': exit_reason,
                'entry_time': pos.entry_time,
                'exit_time': time.time(),
                'max_pnl_risk': pos.highest_pnl_risk,
            }
            self.close_position(symbol, pnl)
            return result
        
        return None
    
    def close_position(self, symbol: str, pnl: float = 0) -> Optional[Position]:
        """Закрыть и удалить позицию."""
        pos = self.positions.pop(symbol, None)
        if pos:
            trade = {
                'symbol': symbol,
                'side': pos.side,
                'entry': pos.entry_price,
                'entry_time': pos.entry_time,
                'pnl': pnl,
                'close_time': time.time(),
            }
            self.closed_trades.append(trade)
            self._save_trade(trade)
            self._delete_position(symbol)
            logger.info(f"CLOSE {pos.side} {symbol} PnL={pnl:.2f}")
        return pos
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """Получить позицию по символу."""
        return self.positions.get(symbol)
    
    def has_position(self, symbol: str) -> bool:
        """Есть ли открытая позиция."""
        return symbol in self.positions
    
    def get_all_positions(self) -> Dict[str, Position]:
        """Все открытые позиции."""
        return dict(self.positions)
    
    def get_closed_trades(self) -> list:
        """История закрытых сделок."""
        return list(self.closed_trades)
    
    def get_stats(self) -> Dict:
        """Общая статистика."""
        trades = list(self.closed_trades)
        if not trades:
            return {'total_trades': 0, 'total_pnl': 0, 'win_rate': 0}
        
        wins = [t for t in trades if t['pnl'] > 0]
        return {
            'total_trades': len(trades),
            'total_pnl': sum(t['pnl'] for t in trades),
            'wins': len(wins),
            'losses': len(trades) - len(wins),
            'win_rate': len(wins) / len(trades) if trades else 0,
            'avg_pnl': sum(t['pnl'] for t in trades) / len(trades),
            'open_positions': len(self.positions),
        }
