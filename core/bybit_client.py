"""
Bybit API Client —连接、签名、基础请求
"""

import time
import hmac
import hashlib
import json
import requests
from typing import Optional, Dict, Any
from urllib.parse import urlencode


class BybitClient:
    """Bybit V5 API Client (Linear Perpetual)"""
    
    BASE_URL = "https://api.bybit.com"
    
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "X-BAPI-API-KEY": self.api_key,
        })
        
        if testnet:
            self.BASE_URL = "https://api-testnet.bybit.com"
        
        self._time_offset = self._sync_time()
    
    def _sync_time(self) -> int:
        """Sync with Bybit server time to avoid timestamp errors."""
        try:
            url = f"{self.BASE_URL}/v5/market/time"
            resp = self.session.get(url, timeout=10)
            server_time = int(resp.json()['result']['timeSecond']) * 1000
            local_time = int(time.time() * 1000)
            offset = server_time - local_time
            return offset
        except Exception:
            return 0

    def _sign(self, params: Dict[str, Any], timestamp: int, use_json: bool = False) -> str:
        """Generate HMAC signature. Bybit V5: sign = HMAC(timestamp + apiKey + recv_window + queryString)."""
        if use_json:
            param_str = json.dumps(params)
        else:
            param_str = urlencode(sorted(params.items()))
        recv_window = 50000
        sign_str = f"{timestamp}{self.api_key}{recv_window}{param_str}"
        return hmac.new(
            self.api_secret.encode(),
            sign_str.encode(),
            hashlib.sha256
        ).hexdigest()
    
    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None,
                 signed: bool = False) -> Dict:
        """Make API request."""
        url = f"{self.BASE_URL}{endpoint}"
        
        if signed:
            timestamp = int(time.time() * 1000) + self._time_offset
            params = params or {}
            params["timestamp"] = timestamp
            use_json = (method == "POST")
            signature = self._sign(params, timestamp, use_json=use_json)
            self.session.headers.update({
                "X-BAPI-API-KEY": self.api_key,
                "X-BAPI-TIMESTAMP": str(timestamp),
                "X-BAPI-SIGN": signature,
                "X-BAPI-RECV-WINDOW": "50000",
            })
        
        if method == "GET":
            resp = self.session.get(url, params=params, timeout=30)
        else:
            resp = self.session.post(url, json=params, timeout=30)
        
        data = resp.json()
        
        if data.get("retCode") != 0:
            raise Exception(f"Bybit API error: {data.get('retMsg', 'Unknown')} (code={data.get('retCode')})")
        
        return data.get("result", {})
    
    # ─── MARKET DATA ─────────────────────────────────────────────
    
    def get_ticker(self, symbol: str) -> Dict:
        """Get latest ticker."""
        result = self._request("GET", "/v5/market/tickers", {"category": "linear", "symbol": symbol})
        return result['list'][0] if result.get('list') else {}
    
    def get_klines(self, symbol: str, interval: str = "60", limit: int = 200,
                   start: int = None, end: int = None) -> list:
        """Get kline/candlestick data."""
        params = {
            "category": "linear", "symbol": symbol, "interval": interval, "limit": limit
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._request("GET", "/v5/market/kline", params)
    
    def get_orderbook(self, symbol: str, limit: int = 1) -> Dict:
        """Get orderbook."""
        return self._request("GET", "/v5/market/orderbook", {
            "category": "linear", "symbol": symbol, "limit": limit
        })
    
    def get_instruments(self, symbol: str) -> Dict:
        """Get instrument info (min qty, tick size, etc)."""
        result = self._request("GET", "/v5/market/instruments-info", {
            "category": "linear", "symbol": symbol
        })
        return result['list'][0] if result.get('list') else {}
    
    # ─── ACCOUNT ─────────────────────────────────────────────────
    
    def get_wallet_balance(self, coin: str = "USDT") -> Dict:
        """Get wallet balance."""
        result = self._request("GET", "/v5/account/wallet-balance", {
            "accountType": "unified", "coin": coin
        }, signed=True)
        return result['list'][0] if result.get('list') else {}
    
    def get_position(self, symbol: str) -> Optional[Dict]:
        """Get current position."""
        result = self._request("GET", "/v5/position/list", {
            "category": "linear", "symbol": symbol
        }, signed=True)
        positions = result.get('list', [])
        if positions and float(positions[0].get('size', 0)) > 0:
            return positions[0]
        return None
    
    def get_positions(self) -> list:
        """Get all positions."""
        result = self._request("GET", "/v5/position/list", {
            "category": "linear"
        }, signed=True)
        return [p for p in result.get('list', []) if float(p.get('size', 0)) > 0]
    
    def get_open_orders(self, symbol: Optional[str] = None) -> list:
        """Get open orders."""
        params = {"category": "linear"}
        if symbol:
            params["symbol"] = symbol
        result = self._request("GET", "/v5/order/realtime", params, signed=True)
        return result.get('list', [])
    
    def get_trade_history(self, symbol: str, limit: int = 50) -> list:
        """Get recent trades."""
        result = self._request("GET", "/v5/execution/list", {
            "category": "linear", "symbol": symbol, "limit": limit
        }, signed=True)
        return result.get('list', [])
    
    # ─── ORDERS ────────────────────────────────────────────────────
    
    def place_market_order(self, symbol: str, side: str, qty: str,
                           reduce_only: bool = False) -> Dict:
        """Place market order. side: Buy/Sell."""
        params = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": qty,
        }
        if reduce_only:
            params["reduceOnly"] = True
        return self._request("POST", "/v5/order/create", params, signed=True)
    
    def place_limit_order(self, symbol: str, side: str, qty: str, price: str,
                          reduce_only: bool = False, time_in_force: str = "GTC") -> Dict:
        """Place limit order."""
        params = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Limit",
            "qty": qty,
            "price": price,
            "timeInForce": time_in_force,
        }
        if reduce_only:
            params["reduceOnly"] = True
        return self._request("POST", "/v5/order/create", params, signed=True)
    
    def place_conditional_order(self, symbol: str, side: str, qty: str,
                                trigger_price: str, order_price: str,
                                order_type: str = "Market",
                                reduce_only: bool = False) -> Dict:
        """Place conditional (trigger) order — stop loss / take profit."""
        params = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": qty,
            "triggerPrice": trigger_price,
            "price": order_price,
            "triggerDirection": 1 if side == "Buy" else 2,
            "reduceOnly": reduce_only,
        }
        return self._request("POST", "/v5/strategy/create", params, signed=True)
    
    def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """Cancel an order."""
        return self._request("POST", "/v5/order/cancel", {
            "category": "linear",
            "symbol": symbol,
            "orderId": order_id,
        }, signed=True)
    
    def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict:
        """Cancel all open orders."""
        params = {"category": "linear"}
        if symbol:
            params["symbol"] = symbol
        return self._request("POST", "/v5/order/cancel-all", params, signed=True)
    
    def cancel_conditional_order(self, symbol: str, order_id: str) -> Dict:
        """Cancel a conditional (trigger) order."""
        return self._request("POST", "/v5/strategy/cancel", {
            "category": "linear",
            "symbol": symbol,
            "orderId": order_id,
        }, signed=True)
    
    def set_leverage(self, symbol: str, leverage: str) -> Dict:
        """Set leverage."""
        return self._request("POST", "/v5/position/set-leverage", {
            "category": "linear",
            "symbol": symbol,
            "buyLeverage": leverage,
            "sellLeverage": leverage,
        }, signed=True)
    
    def set_trading_stop(self, symbol: str, stop_loss: Optional[str] = None,
                         take_profit: Optional[str] = None,
                         trailing_stop: Optional[str] = None,
                         tp_trigger_by: str = "LastPrice",
                         sl_trigger_by: str = "LastPrice") -> Dict:
        """Set SL/TP on existing position."""
        params = {"category": "linear", "symbol": symbol}
        if stop_loss:
            params["stopLoss"] = stop_loss
            params["slTriggerBy"] = sl_trigger_by
        if take_profit:
            params["takeProfit"] = take_profit
            params["tpTriggerBy"] = tp_trigger_by
        if trailing_stop:
            params["trailingStop"] = trailing_stop
        return self._request("POST", "/v5/position/trading-stop", params, signed=True)
