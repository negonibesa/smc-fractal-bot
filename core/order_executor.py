"""
Order Executor —выполнение ордеров: вход, выход, стоп, тейк, трейлинг
"""

import logging
import time as _time
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    success: bool
    order_id: str = ""
    message: str = ""
    fill_price: float = 0.0
    filled_qty: float = 0.0
    sl_tp_ok: bool = True


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
        """Получить параметры инструмента (min qty, max qty, step, tick)."""
        info = self.client.get_instruments(symbol)
        lot_filter = info.get('lotSizeFilter', {})
        price_filter = info.get('priceFilter', {})
        return {
            'min_qty': float(lot_filter.get('minOrderQty', 0.01)),
            'max_qty': float(lot_filter.get('maxMktOrderQty', lot_filter.get('maxOrderQty', 999999))),
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
            # 0. Проверить pending ордера — пропускаем вход если есть
            try:
                pending = self.client.get_open_orders(symbol)
                if pending:
                    logger.warning(f"ENTRY BLOCKED: {symbol} has {len(pending)} pending orders, skipping")
                    return OrderResult(False, message=f"Pending orders exist for {symbol}")
            except Exception as e:
                logger.warning(f"Pending order check failed: {e}")

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
            
            if size > inst['max_qty']:
                logger.warning(f"Size {size} capped to max_qty {inst['max_qty']}")
                size = inst['max_qty']
            
            # 3. Округлить
            qty_str = self._round_qty(size, inst['qty_step'])
            
            # 4. Market order
            logger.info(f"EXEC {side} {qty_str} {symbol} @ market")
            result = self.client.place_market_order(symbol, side, qty_str)
            order_id = result.get('orderId', '')

            # 4b. Verify fill status (detect stuck orders)
            for _attempt in range(5):
                try:
                    orders = self.client.get_open_orders(symbol)
                    unfilled = [o for o in orders if o.get('orderId') == order_id]
                    if not unfilled:
                        break  # filled or gone
                    logger.warning(f"Order {order_id} still pending (attempt {_attempt+1}/5)")
                    _time.sleep(2)
                except Exception:
                    break
            else:
                # After 5 attempts still pending — cancel and report
                logger.error(f"Order {order_id} stuck after 5 checks, cancelling")
                try:
                    self.client.cancel_order(symbol, order_id)
                except Exception:
                    pass
                return OrderResult(False, order_id=order_id, message=f"Order stuck: {order_id}")
            
            # 5. Ставим SL + TP
            sl_str = self._round_price(stop, inst['tick_size'])
            tp_str = self._round_price(tp, inst['tick_size'])
            opposite_side = "Sell" if side == "Buy" else "Buy"

            # Validate TP vs current price
            current_price = float(self.client.get_ticker(symbol).get('lastPrice', 0))
            if side == "Buy" and current_price > 0:
                min_tp = current_price * 1.001  # 0.1% above current
                if float(tp_str) < min_tp:
                    tp_str = self._round_price(min_tp, inst['tick_size'])
                    logger.info(f"TP adjusted for BUY: {tp_str} (was below current {current_price})")
            elif side == "Sell" and current_price > 0:
                max_tp = current_price * 0.999  # 0.1% below current
                if float(tp_str) > max_tp:
                    tp_str = self._round_price(max_tp, inst['tick_size'])
                    logger.info(f"TP adjusted for SELL: {tp_str} (was above current {current_price})")
            
            sl_tp_ok = True
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
                try:
                    self._place_conditional_sl_tp(symbol, opposite_side, qty_str, sl_str, tp_str, inst)
                except Exception as e2:
                    logger.error(f"Conditional SL/TP also failed: {e2}")
                    sl_tp_ok = False
            
            if not sl_tp_ok:
                logger.error(f"SL/TP FAILED for {symbol} — closing position immediately")
                close_success = False
                for attempt in range(3):
                    try:
                        close_side = opposite_side
                        self.client.place_market_order(symbol, close_side, qty_str, reduce_only=True)
                        logger.info(f"Emergency close OK on attempt {attempt+1}: {symbol}")
                        close_success = True
                        break
                    except Exception as e3:
                        logger.error(f"Emergency close attempt {attempt+1} failed: {e3}")
                        _time.sleep(2 * (attempt + 1))
                if not close_success:
                    logger.critical(f"EMERGENCY CLOSE FAILED AFTER 3 ATTEMPTS: {symbol} — NAKED POSITION!")
                    return OrderResult(False, order_id=order_id, message=f"Naked position! All close attempts failed", filled_qty=float(qty_str), sl_tp_ok=False)
            
            return OrderResult(True, order_id=order_id, message=f"Opened {side} {qty_str}", filled_qty=float(qty_str), sl_tp_ok=sl_tp_ok)
        
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
        """Закрыть текущую позицию маркет-ордером с проверкой исполнения."""
        try:
            pos = self.client.get_position(symbol)
            if not pos:
                return OrderResult(False, message="No position")
            
            pos_side = pos.get('side', '')
            size = pos.get('size', '0')
            inst = self._get_instrument(symbol)
            
            if side is None:
                close_side = "Sell" if pos_side == "Buy" else "Buy"
            else:
                close_side = side
            
            qty_str = self._round_qty(float(size), inst['qty_step'])
            
            logger.info(f"CLOSE {close_side} {qty_str} {symbol}")
            result = self.client.place_market_order(
                symbol, close_side, qty_str, reduce_only=True
            )
            
            # Verify execution: poll position status
            closed_ok = False
            for attempt in range(3):
                _time.sleep(1)
                check_pos = self.client.get_position(symbol)
                check_size = float(check_pos.get('size', 0)) if check_pos else 0
                if check_size == 0:
                    closed_ok = True
                    break
                logger.warning(f"Close verify {attempt+1}: {symbol} still has size={check_size}")
            
            if not closed_ok:
                logger.error(f"Close NOT verified: {symbol} may still be open!")
                return OrderResult(False, message="Close not verified - position may still be open")
            
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
