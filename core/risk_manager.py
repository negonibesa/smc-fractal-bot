"""
Risk Manager —размер позиции, лимиты безопасности, трейлинг
"""

import time
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass, field
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class DailyStats:
    """Дневная статистика для лимитов."""
    date: str = ""
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    consecutive_losses: int = 0
    max_drawdown_today: float = 0.0


class RiskManager:
    """Управление рисками и лимиты безопасности."""
    
    def __init__(self, risk_percent: float = 1.0, max_drawdown: float = 10.0,
                 max_daily_loss: float = 5.0, max_consecutive_losses: int = 3,
                 max_daily_trades: int = 20, commission: float = 0.001,
                 slippage: float = 0.0005, stop_buffer: float = 0.002,
                 redis_store=None):
        """
        Args:
            risk_percent: Риск на сделку (% от equity)
            max_drawdown: Макс просадка (% — стоп торговли)
            max_daily_loss: Макс дневной убыток (% — стоп на день)
            max_consecutive_losses: Макс серия стопов подряд
            max_daily_trades: Макс сделок в день
            commission: Комиссия (0.1%)
            slippage: Проскальзывание (0.05%)
            stop_buffer: Буфер на стоп (0.2%)
            redis_store: RedisStore instance for persistence
        """
        self.risk_percent = risk_percent
        self.max_drawdown = max_drawdown
        self.max_daily_loss = max_daily_loss
        self.max_consecutive_losses = max_consecutive_losses
        self.max_daily_trades = max_daily_trades
        self.commission = commission
        self.slippage = slippage
        self.stop_buffer = stop_buffer
        self.redis = redis_store
        
        # Состояние
        self.initial_equity = 0
        self.peak_equity = 0
        self.daily = DailyStats(date=time.strftime("%Y-%m-%d"))
        self.recent_trades: deque = deque(maxlen=100)
        self.daily_history: List[DailyStats] = []
        self._trading_paused = False
        self._pause_reason = ""
        
        # Restore from Redis
        if self.redis:
            self._restore_from_redis()

    def _restore_from_redis(self):
        """Load risk state from Redis."""
        try:
            state = self.redis.load_risk_state()
            if state:
                self.initial_equity = state.get('initial_equity', 0)
                self.peak_equity = state.get('peak_equity', 0)
                self._trading_paused = state.get('trading_paused', False)
                self._pause_reason = state.get('pause_reason', '')
                
                daily = state.get('daily', {})
                if daily.get('date') == time.strftime("%Y-%m-%d"):
                    self.daily = DailyStats(
                        date=daily['date'],
                        trades=daily.get('trades', 0),
                        wins=daily.get('wins', 0),
                        losses=daily.get('losses', 0),
                        pnl=daily.get('pnl', 0.0),
                        consecutive_losses=daily.get('consecutive_losses', 0),
                    )
                logger.info(f"RESTORE RISK: equity={self.peak_equity:.2f} "
                           f"trades={self.daily.trades} pnl={self.daily.pnl:.2f}")
        except Exception as e:
            logger.error(f"Redis restore risk failed: {e}")

    def _save_to_redis(self):
        """Persist risk state to Redis."""
        if not self.redis:
            return
        try:
            self.redis.save_risk_state({
                'initial_equity': self.initial_equity,
                'peak_equity': self.peak_equity,
                'trading_paused': self._trading_paused,
                'pause_reason': self._pause_reason,
                'daily': {
                    'date': self.daily.date,
                    'trades': self.daily.trades,
                    'wins': self.daily.wins,
                    'losses': self.daily.losses,
                    'pnl': self.daily.pnl,
                    'consecutive_losses': self.daily.consecutive_losses,
                },
            })
        except Exception as e:
            logger.error(f"Redis save risk failed: {e}")
    
    def update_equity(self, equity: float):
        """Обновить equity (вызывать каждый цикл)."""
        if self.initial_equity == 0:
            self.initial_equity = equity
        
        self.peak_equity = max(self.peak_equity, equity)
        
        # Сброс дневной статистики
        today = time.strftime("%Y-%m-%d")
        if today != self.daily.date:
            self.daily_history.append(self.daily)
            self.daily = DailyStats(date=today)
        
        self._save_to_redis()
    
    def can_trade(self) -> tuple[bool, str]:
        """
        Проверить, можно ли торговать.
        
        Returns:
            (can_trade, reason)
        """
        if self._trading_paused:
            return False, self._pause_reason
        
        # 1. Проверка просадки
        if self.peak_equity > 0 and self.initial_equity > 0:
            dd = (self.peak_equity - self._current_equity()) / self.peak_equity * 100
            if dd >= self.max_drawdown:
                self._pause(f"Max drawdown {dd:.1f}% >= {self.max_drawdown}%")
                return False, self._pause_reason
        
        # 2. Дневной лимит сделок
        if self.daily.trades >= self.max_daily_trades:
            return False, f"Daily trades limit: {self.daily.trades}/{self.max_daily_trades}"
        
        # 3. Дневной убыток
        if self.initial_equity > 0 and self.daily.pnl < 0:
            loss_pct = abs(self.daily.pnl) / self.initial_equity * 100
            if loss_pct >= self.max_daily_loss:
                return False, f"Daily loss {loss_pct:.1f}% >= {self.max_daily_loss}%"
        
        # 4. Серия стопов
        if self.daily.consecutive_losses >= self.max_consecutive_losses:
            return False, f"Consecutive losses: {self.daily.consecutive_losses}/{self.max_consecutive_losses}"
        
        return True, "OK"
    
    def calculate_position_size(self, entry: float, stop: float,
                                equity: float, risk_percent: Optional[float] = None,
                                qty_step: float = 0.01) -> float:
        """
        Рассчитать размер позиции.
        
        Formula: size = risk_amount / stop_distance
        
        Args:
            entry: Цена входа
            stop: Стоп-лосс
            equity: Текущий баланс
            risk_percent: % риск (если None — использует self.risk_percent)
            qty_step: Шаг размера
        
        Returns:
            Количество
        """
        risk_pct = risk_percent or self.risk_percent
        risk_amount = equity * (risk_pct / 100)
        stop_distance = abs(entry - stop)
        
        if stop_distance == 0:
            return 0
        
        # Учитываем комиссию × 2 (вход + выход)
        commission_cost = equity * self.commission * 2
        net_risk = risk_amount - commission_cost * 0.1  # небольшой запас
        
        size = net_risk / stop_distance
        
        # Округляем вниз до qty_step
        if qty_step > 0:
            size = int(size / qty_step) * qty_step
        
        return max(0, round(size, 8))
    
    def calculate_sl_tp(self, entry: float, stop: float, tp_multiplier: float = 1.0,
                        direction: str = "LONG") -> tuple[float, float]:
        """
        Рассчитать SL и TP с учётом буфера и комиссии.
        
        Returns:
            (stop_price, tp_price)
        """
        risk = abs(entry - stop)
        buffer = risk * self.stop_buffer
        
        if direction == "LONG":
            sl = stop - buffer
            tp = entry + risk * tp_multiplier
        else:
            sl = stop + buffer
            tp = entry - risk * tp_multiplier
        
        # Компенсация комиссии
        commission_entry = entry * self.commission
        commission_exit = entry * self.commission
        total_commission = commission_entry + commission_exit
        
        if direction == "LONG":
            sl -= total_commission
            tp += total_commission
        else:
            sl += total_commission
            tp -= total_commission
        
        return sl, tp
    
    def trailing_stop(self, entry: float, current_price: float, original_stop: float,
                      highest_pnl_risk: float, direction: str = "LONG",
                      breakeven_at: float = 0.5, trail_activate: float = 1.0,
                      trail_step: float = 0.5) -> tuple[float, float]:
        """
        Обновить стоп (breakeven + trailing).
        
        Returns:
            (new_stop, updated_highest_pnl_risk)
        """
        risk_unit = abs(entry - original_stop)
        if risk_unit == 0:
            return original_stop, highest_pnl_risk
        
        # Текущий PnL в единицах риска
        if direction == "LONG":
            current_risk = (current_price - entry) / risk_unit
        else:
            current_risk = (entry - current_price) / risk_unit
        
        new_highest = max(highest_pnl_risk, current_risk)
        new_stop = original_stop
        
        # Breakeven
        if breakeven_at > 0 and new_highest >= breakeven_at:
            if direction == "LONG":
                be_stop = entry + entry * self.commission
                new_stop = max(new_stop, be_stop)
            else:
                be_stop = entry - entry * self.commission
                new_stop = min(new_stop, be_stop)
        
        # Trailing
        if trail_activate > 0 and trail_step > 0 and new_highest >= trail_activate:
            trail_distance = risk_unit * trail_step
            if direction == "LONG":
                trail_stop = current_price - trail_distance
                new_stop = max(new_stop, trail_stop)
            else:
                trail_stop = current_price + trail_distance
                new_stop = min(new_stop, trail_stop)
        
        return new_stop, new_highest
    
    def register_trade(self, pnl: float):
        """Зарегистрировать результат сделки."""
        self.daily.trades += 1
        self.daily.pnl += pnl
        
        if pnl > 0:
            self.daily.wins += 1
            self.daily.consecutive_losses = 0
        else:
            self.daily.losses += 1
            self.daily.consecutive_losses += 1
        
        self.recent_trades.append({
            'time': time.time(),
            'pnl': pnl,
            'daily_trades': self.daily.trades,
        })
        
        self._save_to_redis()
    
    def _current_equity(self) -> float:
        """Текущая equity (из последней сделки или initial)."""
        if self.recent_trades:
            return self.peak_equity  # approximation
        return self.initial_equity
    
    def _pause(self, reason: str):
        """Приостановить торговлю."""
        self._trading_paused = True
        self._pause_reason = reason
        logger.critical(f"TRADING PAUSED: {reason}")
    
    def resume(self):
        """Возобновить торговлю."""
        self._trading_paused = False
        self._pause_reason = ""
        logger.info("Trading resumed")
    
    def reset_daily(self):
        """Принудительный сброс дневных лимитов."""
        self.daily = DailyStats(date=time.strftime("%Y-%m-%d"))
        self._trading_paused = False
        self._pause_reason = ""
        self._save_to_redis()
        logger.info("Daily stats reset")
    
    def get_status(self) -> Dict:
        """Получить текущий статус риск-менеджера."""
        dd = 0
        if self.peak_equity > 0 and self.initial_equity > 0:
            dd = (self.peak_equity - self._current_equity()) / self.peak_equity * 100
        
        return {
            'equity': self._current_equity(),
            'peak_equity': self.peak_equity,
            'drawdown_pct': dd,
            'daily_trades': self.daily.trades,
            'daily_pnl': self.daily.pnl,
            'daily_wins': self.daily.wins,
            'daily_losses': self.daily.losses,
            'consecutive_losses': self.daily.consecutive_losses,
            'can_trade': self.can_trade()[0],
            'pause_reason': self._pause_reason,
        }
