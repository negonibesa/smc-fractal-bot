"""
Position Tracker —отслеживание открытых позиций и trailing stop
"""

import time
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass, field
from collections import deque

logger = logging.getLogger(__name__)

BAR_SECONDS_4H = 4 * 3600


def bars_held(entry_bar_ts: float, bar_ts: float) -> int:
    """Сколько закрытых 4H-баров прошло с бара входа.

    Считается по времени, а не по счётчику в памяти, чтобы time-stop работал
    после рестарта бота. Определено здесь, а не импортом из donchian_breakout,
    чтобы отказ модуля стратегии не уронил PositionTracker, который держит
    ещё и живые smc/zdev-позиции. Паритет с бэктестом проверяет
    test_donchian_parity.py.
    """
    if not entry_bar_ts or not bar_ts:
        return 0
    return int((bar_ts - entry_bar_ts) // BAR_SECONDS_4H)


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
    strategy: str = 'smc'  # smc / zdev / donchian
    # Donchian: состояние ATR-трейлинга и time-stop. Хранится в Redis, иначе
    # рестарт бота сбросил бы экстремум и ATR входа и стратегия поехала бы
    # с другими стопами, чем валидировались в бэктесте.
    extreme: float = 0.0        # максимум (LONG) / минимум (SHORT) с входа
    atr_entry: float = 0.0      # ATR, зафиксированный на баре входа
    entry_bar_ts: float = 0.0   # время закрытия бара входа
    max_bars: int = 0           # 0 = time-stop выключен
    
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
    
    def __init__(self, redis_store=None):
        """
        Args:
            redis_store: RedisStore instance for persistence
        """
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
                side = p['side']
                # Convert old format
                if side == 'BUY':
                    side = 'LONG'
                elif side == 'SELL':
                    side = 'SHORT'
                pos = Position(
                    symbol=p['symbol'],
                    side=side,
                    entry_price=p['entry_price'],
                    size=p['size'],
                    stop_price=p['stop_price'],
                    tp_price=p['tp_price'],
                    entry_time=p['entry_time'],
                    highest_pnl_risk=p.get('highest_pnl_risk', 0.0),
                    original_stop=p.get('original_stop', p['stop_price']),
                    trailing_active=p.get('trailing_active', False),
                    strategy=p.get('strategy', 'smc'),
                    extreme=p.get('extreme', p['entry_price']),
                    atr_entry=p.get('atr_entry', 0.0),
                    entry_bar_ts=p.get('entry_bar_ts', 0.0),
                    max_bars=p.get('max_bars', 0),
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
                'strategy': pos.strategy,
                'extreme': pos.extreme,
                'atr_entry': pos.atr_entry,
                'entry_bar_ts': pos.entry_bar_ts,
                'max_bars': pos.max_bars,
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
                      size: float, stop: float, tp: float,
                      strategy: str = 'smc', atr_entry: float = 0.0,
                      entry_bar_ts: float = 0.0,
                      max_bars: int = 0) -> Position:
        """Зарегистрировать открытие позиции.

        tp=None допустим: у Donchian фиксированного TP нет.
        """
        pos = Position(
            symbol=symbol,
            side=side,
            entry_price=entry,
            size=size,
            stop_price=stop,
            tp_price=tp,
            entry_time=time.time(),
            original_stop=stop,
            strategy=strategy,
            extreme=entry,
            atr_entry=atr_entry,
            entry_bar_ts=entry_bar_ts,
            max_bars=max_bars,
        )
        self.positions[symbol] = pos
        self._save_position(symbol, pos)
        logger.info(f"TRACK OPEN: {strategy} {side} {size} {symbol} @ {entry} "
                    f"SL={stop} TP={tp if tp else '—'}")
        return pos
    
    def check_exits(self, symbol: str, high: float, low: float,
                    close: float, bar_ts: float = None,
                    max_bars: int = None) -> Optional[Dict]:
        """
        Проверить срабатывание SL/TP/time-stop.

        bar_ts и max_bars заполняются только для Donchian. У smc/zdev они
        остаются None, поэтому поведение этих стратегий не меняется.

        Returns:
            dict с результатом если позиция закрыта, иначе None
        """
        pos = self.positions.get(symbol)
        if not pos:
            return None
        
        exit_price = None
        exit_reason = None
        has_tp = pos.tp_price is not None and pos.tp_price > 0
        
        if pos.side == "LONG":
            if low <= pos.stop_price:
                exit_price = pos.stop_price
                exit_reason = "STOP_LOSS"
                if pos.trailing_active:
                    exit_reason = "TRAILING_STOP"
                elif pos.stop_price > pos.original_stop and \
                     abs(pos.stop_price - pos.entry_price) < pos.risk_unit * 0.1:
                    exit_reason = "BREAKEVEN"
            elif has_tp and high >= pos.tp_price:
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
            elif has_tp and low <= pos.tp_price:
                exit_price = pos.tp_price
                exit_reason = "TAKE_PROFIT"

        # Time-stop после стопа: в баре, где сработали оба, приоритет у стопа.
        if exit_price is None and max_bars and pos.max_bars:
            if bars_held(pos.entry_bar_ts, bar_ts) >= pos.max_bars:
                exit_price = close
                exit_reason = "TIME_STOP"
        
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
                'strategy': pos.strategy,
                'entry_time': pos.entry_time,
                'exit_time': time.time(),
                'max_pnl_risk': pos.highest_pnl_risk,
            }
            self.close_position(symbol, pnl, exit_price=exit_price,
                                exit_reason=exit_reason, size=pos.size)
            return result
        
        return None
    
    def close_position(self, symbol: str, pnl: float = 0,
                       exit_price: float = 0, exit_reason: str = "",
                       size: float = 0) -> Optional[Position]:
        """Закрыть и удалить позицию."""
        pos = self.positions.pop(symbol, None)
        if pos:
            trade = {
                'symbol': symbol,
                'side': pos.side,
                'entry': pos.entry_price,
                'entry_time': pos.entry_time,
                'exit_price': exit_price if exit_price else pos.entry_price,
                'exit_reason': exit_reason,
                'size': size if size else pos.size,
                'pnl': pnl,
                'close_time': time.time(),
                'max_pnl_risk': pos.highest_pnl_risk,
                'stop_price': pos.original_stop,
                'strategy': pos.strategy,
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
