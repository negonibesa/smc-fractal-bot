"""
Redis Store — persistent storage for bot state.
Stores positions, trades, risk stats, signal state between restarts.
"""

import json
import time
import logging
from typing import Optional, Dict, List
from dataclasses import asdict

logger = logging.getLogger(__name__)

# Key prefixes
K_POS = "bot:positions:{symbol}"
K_POSITIONS_INDEX = "bot:positions:index"
K_CLOSED_TRADES = "bot:trades:closed"
K_TRADE_LOG = "bot:trades:log"
K_RISK = "bot:risk"
K_SIGNAL = "bot:signal:{symbol}"
K_DAILY = "bot:daily"
K_META = "bot:meta"


class RedisStore:
    """Redis-backed persistent storage for all bot state."""

    def __init__(self, host: str = "localhost", port: int = 6379,
                 db: int = 0, password: str = None, prefix: str = "smc"):
        import redis
        self.r = redis.Redis(
            host=host, port=port, db=db,
            password=password, decode_responses=True,
            socket_connect_timeout=5, socket_timeout=5,
        )
        self.prefix = prefix
        self._test_connection()

    def _test_connection(self):
        try:
            self.r.ping()
            logger.info("Redis connected")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            raise

    def _key(self, template: str, **kwargs) -> str:
        return f"{self.prefix}:{template.format(**kwargs)}"

    # ─── POSITIONS ──────────────────────────────────────────────

    def save_position(self, symbol: str, pos_dict: dict):
        """Save open position to Redis."""
        key = self._key("pos:{symbol}", symbol=symbol)
        self.r.set(key, json.dumps(pos_dict))
        self.r.sadd(self._key("pos:index"), symbol)
        logger.debug(f"Redis SAVE position {symbol}")

    def load_position(self, symbol: str) -> Optional[dict]:
        """Load position from Redis."""
        key = self._key("pos:{symbol}", symbol=symbol)
        data = self.r.get(key)
        if data:
            return json.loads(data)
        return None

    def load_all_positions(self) -> Dict[str, dict]:
        """Load all open positions."""
        symbols = self.r.smembers(self._key("pos:index"))
        result = {}
        for symbol in symbols:
            pos = self.load_position(symbol)
            if pos:
                result[symbol] = pos
        return result

    def delete_position(self, symbol: str):
        """Remove position from Redis."""
        key = self._key("pos:{symbol}", symbol=symbol)
        self.r.delete(key)
        self.r.srem(self._key("pos:index"), symbol)
        logger.debug(f"Redis DELETE position {symbol}")

    def clear_all_positions(self):
        """Clear all positions."""
        symbols = self.r.smembers(self._key("pos:index"))
        for symbol in symbols:
            self.delete_position(symbol)

    # ─── CLOSED TRADES ─────────────────────────────────────────

    def save_closed_trade(self, trade: dict):
        """Append closed trade to history."""
        key = self._key("trades:closed")
        self.r.rpush(key, json.dumps(trade))
        # Keep last 1000 trades
        self.r.ltrim(key, -1000, -1)

    def load_closed_trades(self, limit: int = 500) -> list:
        """Load recent closed trades."""
        key = self._key("trades:closed")
        data = self.r.lrange(key, -limit, -1)
        return [json.loads(d) for d in data]

    def get_trade_count(self) -> int:
        """Total number of closed trades."""
        return self.r.llen(self._key("trades:closed"))

    def clear_trades(self):
        """Clear all trade history."""
        self.r.delete(self._key("trades:closed"))

    # ─── TRADE LOG ─────────────────────────────────────────────

    def save_trade_log(self, entry: dict):
        """Append to trade log."""
        key = self._key("trades:log")
        self.r.rpush(key, json.dumps(entry))
        self.r.ltrim(key, -5000, -1)

    def load_trade_log(self, limit: int = 1000) -> list:
        """Load recent log entries."""
        key = self._key("trades:log")
        data = self.r.lrange(key, -limit, -1)
        return [json.loads(d) for d in data]

    # ─── RISK MANAGER STATE ────────────────────────────────────

    def save_risk_state(self, state: dict):
        """Save risk manager state."""
        key = self._key("risk")
        self.r.set(key, json.dumps(state))

    def load_risk_state(self) -> Optional[dict]:
        """Load risk manager state."""
        key = self._key("risk")
        data = self.r.get(key)
        if data:
            return json.loads(data)
        return None

    def clear_risk_state(self):
        """Clear risk state."""
        self.r.delete(self._key("risk"))

    # ─── SIGNAL STATE ──────────────────────────────────────────

    def save_signal_state(self, symbol: str, state: dict):
        """Save signal generator state for a symbol."""
        key = self._key("signal:{symbol}", symbol=symbol)
        self.r.set(key, json.dumps(state))

    def load_signal_state(self, symbol: str) -> Optional[dict]:
        """Load signal generator state."""
        key = self._key("signal:{symbol}", symbol=symbol)
        data = self.r.get(key)
        if data:
            return json.loads(data)
        return None

    def clear_signal_states(self):
        """Clear all signal states."""
        for key in self.r.scan_iter(f"{self.prefix}:signal:*"):
            self.r.delete(key)

    # ─── DAILY STATS ───────────────────────────────────────────

    def save_daily(self, daily: dict):
        """Save daily stats."""
        key = self._key("daily")
        self.r.set(key, json.dumps(daily))

    def load_daily(self) -> Optional[dict]:
        """Load daily stats."""
        key = self._key("daily")
        data = self.r.get(key)
        if data:
            return json.loads(data)
        return None

    # ─── META ──────────────────────────────────────────────────

    def save_meta(self, data: dict):
        """Save bot metadata (start time, version, etc)."""
        key = self._key("meta")
        self.r.set(key, json.dumps(data))

    def load_meta(self) -> Optional[dict]:
        """Load bot metadata."""
        key = self._key("meta")
        data = self.r.get(key)
        if data:
            return json.loads(data)
        return None

    # ─── BULK OPERATIONS ───────────────────────────────────────

    def save_full_state(self, positions: dict, trades: list,
                        risk_state: dict, signal_states: dict):
        """Save complete bot state atomically."""
        pipe = self.r.pipeline()

        # Positions
        pipe.delete(self._key("pos:index"))
        for sym, pos in positions.items():
            pipe.set(self._key(f"pos:{sym}"), json.dumps(pos))
            pipe.sadd(self._key("pos:index"), sym)

        # Risk
        if risk_state:
            pipe.set(self._key("risk"), json.dumps(risk_state))

        # Signals
        for sym, state in signal_states.items():
            pipe.set(self._key(f"signal:{sym}"), json.dumps(state))

        pipe.execute()
        logger.info(f"Redis: saved full state ({len(positions)} positions)")

    def load_full_state(self) -> dict:
        """Load complete bot state."""
        return {
            'positions': self.load_all_positions(),
            'trades': self.load_closed_trades(),
            'risk': self.load_risk_state(),
            'signals': {},  # loaded per symbol
        }

    def get_status(self) -> dict:
        """Redis connection status and key counts."""
        try:
            info = self.r.info("keyspace")
            return {
                'connected': True,
                'keys': self.r.dbsize(),
                'positions': len(self.r.smembers(self._key("pos:index"))),
                'trades': self.r.llen(self._key("trades:closed")),
            }
        except Exception as e:
            return {'connected': False, 'error': str(e)}

    def flush(self):
        """Clear ALL bot data (careful!)."""
        keys = list(self.r.scan_iter(f"{self.prefix}:*"))
        if keys:
            self.r.delete(*keys)
        logger.warning(f"Redis: flushed {len(keys)} keys")
