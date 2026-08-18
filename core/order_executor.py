"""
Order Executor —выполнение ордеров: вход, выход, стоп, тейк, трейлинг
"""

import logging
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    success: bool
    order_id: str = ""
    message: str = ""
    fill_price: float = 0.0


class OrderExecutor:
    """Исполнение ордеров через Bybit API."""
    
    def __init__(self, client, risk_manager):
        """
        Args:
            client: BybitClient instance
            risk_manager: RiskManager instance
        """
        self.client = client
        self.risk = risk_manager
    
    def _round_qty(self, qty: float, step_size: float) -> str:
        """Округлить количество до шага."""
        if step_size <= 0:
            return f"{qty:.4f}"
        qty = int(qty / step_size) * step_size
        return f"{qty:.4f}".rstrip('0').rstrip('.')
    
    def _round_price(self, price: float, tick_size: float) -> str:
        """Округлить цену до tick size."""
        if tick_size <= 0:
            return f"{price:.4f}"
        price = int(price / tick_size) * tick_size
        return f"{price:.4f}".rstrip('0').rstrip('.')
    
    def _get_instrument(self, symbol: str) -> Dict:
        """Получить параметры инструмента (min qty, step, tick)."""
        info = self.client.get_instruments(symbol)
        lot_filter = info.get('lotSizeFilter', {})
        price_filter = info.get('priceFilter', {})
        return {
            'min_qty': float(lot_filter.get('minOrderQty', 0.01)),
            'qty_step': float(lot_filter.get('qtyStep', 0.01)),
            'min_value': float(lot_filter.get('minNotionalValue', 5)),
            'tick_size': float(price_filter.get('tickSize', 0.01)),
        }
    
    def open_long(self, symbol: str, entry: float, stop: float, tp: float,
                  risk_percent: Optional[float] = None) -> OrderResult:
        """
        Открыть LONG: маркет + стоп + тейк.
        
        Returns: OrderResult
        """
        return self._execute_entry(symbol, "Buy", entry, stop, tp, risk_percent)
    
    def open_short(self, symbol: str, entry: float, stop: float, tp: float,
                   risk_percent: Optional[float] = None) -> OrderResult:
        """
        Открыть SHORT: маркет + стоп + тейк.
        
        Returns: OrderResult
        """
        return self._execute_entry(symbol, "Sell", entry, stop, tp, risk_percent)
    
    def _execute_entry(self, symbol: str, side: str, entry: float,
                       stop: float, tp: float,
                       risk_percent: Optional[float] = None) -> OrderResult:
        """Общий метод входа: расчёт размера → маркет → SL/TP."""
        try:
            # 1. Получить параметры инструмента
            inst = self._get_instrument(symbol)
            
            # 2. Рассчитать размер позиции
            balance = self.client.get_wallet_balance()
            equity = float(balance.get('totalEquity', 0))
            
            if equity <= 0:
                return OrderResult(False, message="No equity")
            
            risk_pct = risk_percent or self.risk.risk_percent
            size = self.risk.calculate_position_size(
                entry, stop, equity, risk_pct, inst['qty_step']
            )
            
            if size < inst['min_qty']:
                return OrderResult(False, message=f"Size {size} < min {inst['min_qty']}")
            
            # 3. Округлить
            qty_str = self._round_qty(size, inst['qty_step'])
            
            # 4. Market order
            logger.info(f"EXEC {side} {qty_str} {symbol} @ market")
            result = self.client.place_market_order(symbol, side, qty_str)
            order_id = result.get('orderId', '')
            
            # 5. Ставим SL + TP
            sl_str = self._round_price(stop, inst['tick_size'])
            tp_str = self._round_price(tp, inst['tick_size'])
            opposite_side = "Sell" if side == "Buy" else "Buy"
            
            try:
                self.client.set_trading_stop(
                    symbol,
                    stop_loss=sl_str,
                    take_profit=tp_str,
                    sl_trigger_by="LastPrice",
                    tp_trigger_by="LastPrice",
                )
                logger.info(f"SL={sl_str} TP={tp_str}")
            except Exception as e:
                logger.warning(f"SL/TP via set_trading_stop failed, using conditional: {e}")
                self._place_conditional_sl_tp(symbol, opposite_side, qty_str, sl_str, tp_str, inst)
            
            return OrderResult(True, order_id=order_id, message=f"Opened {side} {qty_str}")
        
        except Exception as e:
            logger.error(f"Entry failed: {e}")
            return OrderResult(False, message=str(e))
    
    def _place_conditional_sl_tp(self, symbol: str, close_side: str,
                                 qty_str: str, sl_str: str, tp_str: str,
                                 inst: dict):
        """Fallback: conditional orders для SL и TP."""
        try:
            self.client.place_conditional_order(
                symbol, close_side, qty_str,
                trigger_price=sl_str, order_price=sl_str,
                order_type="Market", reduce_only=True
            )
        except Exception as e:
            logger.error(f"Conditional SL failed: {e}")
        
        try:
            self.client.place_conditional_order(
                symbol, close_side, qty_str,
                trigger_price=tp_str, order_price=tp_str,
                order_type="Market", reduce_only=True
            )
        except Exception as e:
            logger.error(f"Conditional TP failed: {e}")
    
    def close_position(self, symbol: str, side: Optional[str] = None) -> OrderResult:
        """Закрыть текущую позицию маркет-ордером."""
        try:
            pos = self.client.get_position(symbol)
            if not pos:
                return OrderResult(False, message="No position")
            
            pos_side = pos.get('side', '')
            size = pos.get('size', '0')
            inst = self._get_instrument(symbol)
            
            # Если side не указан — закрываем противоположным
            if side is None:
                close_side = "Sell" if pos_side == "Buy" else "Buy"
            else:
                close_side = side
            
            qty_str = self._round_qty(float(size), inst['qty_step'])
            
            logger.info(f"CLOSE {close_side} {qty_str} {symbol}")
            result = self.client.place_market_order(
                symbol, close_side, qty_str, reduce_only=True
            )
            
            # Удаляем conditional ордера
            self._cancel_conditional_orders(symbol)
            
            return OrderResult(True, order_id=result.get('orderId', ''), message="Closed")
        
        except Exception as e:
            logger.error(f"Close failed: {e}")
            return OrderResult(False, message=str(e))
    
    def update_stop_loss(self, symbol: str, new_stop: float) -> OrderResult:
        """Обновить стоп-лосс (для trailing/breakeven)."""
        try:
            inst = self._get_instrument(symbol)
            sl_str = self._round_price(new_stop, inst['tick_size'])
            
            logger.info(f"UPDATE SL → {sl_str}")
            self.client.set_trading_stop(symbol, stop_loss=sl_str)
            return OrderResult(True, message=f"SL updated to {sl_str}")
        
        except Exception as e:
            logger.error(f"Update SL failed: {e}")
            return OrderResult(False, message=str(e))
    
    def update_take_profit(self, symbol: str, new_tp: float) -> OrderResult:
        """Обновить тейк-профит."""
        try:
            inst = self._get_instrument(symbol)
            tp_str = self._round_price(new_tp, inst['tick_size'])
            
            self.client.set_trading_stop(symbol, take_profit=tp_str)
            return OrderResult(True, message=f"TP updated to {tp_str}")
        
        except Exception as e:
            logger.error(f"Update TP failed: {e}")
            return OrderResult(False, message=str(e))
    
    def _cancel_conditional_orders(self, symbol: str):
        """Удалить все conditional ордера по символу."""
        try:
            orders = self.client.get_open_orders(symbol)
            for o in orders:
                if o.get('orderType') in ['Conditional', 'StopLoss', 'TakeProfit',
                                          'StopMark', 'TakeProfitMark',
                                          'TrailingStop', 'PartialStopLoss']:
                    self.client.cancel_conditional_order(symbol, o['orderId'])
        except Exception as e:
            logger.warning(f"Cancel conditional orders failed: {e}")
    
    def cancel_all(self, symbol: Optional[str] = None) -> bool:
        """Отменить все ордера."""
        try:
            self.client.cancel_all_orders(symbol)
            return True
        except Exception as e:
            logger.error(f"Cancel all failed: {e}")
            return False
    
    def set_leverage(self, symbol: str, leverage: int = 10) -> bool:
        """Установить плечо."""
        try:
            self.client.set_leverage(symbol, str(leverage))
            logger.info(f"Leverage set to {leverage}x for {symbol}")
            return True
        except Exception as e:
            logger.error(f"Set leverage failed: {e}")
            return False
