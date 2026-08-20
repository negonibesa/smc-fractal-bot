"""
SMC Fractal Bot — Main Trading Loop
Принцип: sweep → return to center → enter OPPOSITE → capture impulse
"""

import os
import sys
import time
import logging
import yaml
import signal
import threading
import pandas as pd
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Добавляем корень проекта в path
sys.path.insert(0, str(Path(__file__).parent))

from core import BybitClient, OrderExecutor, RiskManager, PositionTracker, TelegramNotifier
from core.redis_store import RedisStore
from core.auto_optimizer import AutoOptimizer
from core.regime_detector import detect_regime, adapt_params_for_regime
from core.reporter import Reporter
from core.dashboard import Dashboard
from smc_features import find_consolidation_center, detect_sweep, calculate_adx

load_dotenv()

# ─── LOGGING ──────────────────────────────────────────────────────
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

from logging.handlers import RotatingFileHandler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(LOG_DIR / "bot.log", maxBytes=5*1024*1024, backupCount=5),
    ]
)
logger = logging.getLogger("bot")

# ─── CLEANUP OLD LOGS ON STARTUP ──────────────────────────────────
import glob as _glob
for old_log in _glob.glob(str(LOG_DIR / "bot.log.*")):
    try:
        Path(old_log).unlink()
    except Exception:
        pass

# ─── CONFIG ────────────────────────────────────────────────────────

def load_config() -> dict:
    """Загрузить конфиг. Multi-pair → settings.yaml, fallback → best.yaml."""
    settings_path = Path(__file__).parent / "config" / "settings.yaml"
    best_path = Path(__file__).parent / "config" / "best.yaml"
    
    # Prefer settings.yaml if it has assets defined
    if settings_path.exists():
        with open(settings_path) as f:
            cfg = yaml.safe_load(f)
        if cfg and cfg.get('assets'):
            logger.info(f"Loading multi-pair config from settings.yaml ({len(cfg['assets'])} assets)")
            return cfg
    
    if best_path.exists():
        logger.info("Loading single-pair config from best.yaml")
        with open(best_path) as f:
            return yaml.safe_load(f)
    
    raise FileNotFoundError("No config found")


# ─── SIGNAL GENERATOR ─────────────────────────────────────────────

class SignalGenerator:
    """Генерация сигналов из live данных."""
    
    def __init__(self, config: dict):
        self.lookback = config['strategy']['lookback']
        self.sweep_threshold = config['strategy']['sweep_threshold']
        self.center_proximity = config['strategy']['center_proximity']
        self.tp_multiplier = config['strategy']['tp_multiplier']
        self.timeout = config['strategy']['timeout']
        self.adx_enabled = config.get('filters', {}).get('adx_filter', {}).get('enabled', False)
        self.adx_min = config.get('filters', {}).get('adx_filter', {}).get('min_adx', 25)
        self.session_enabled = config.get('filters', {}).get('session_filter', {}).get('enabled', False)
        self.trend_1d_enabled = config.get('filters', {}).get('trend_1d_filter', {}).get('enabled', False)
        
        # Состояние
        self.state = 0  # 0 = ждём sweep, 1 = ждём return to center
        self.sweep_direction = None
        self.sweep_price = None
        self.sweep_index = None
        self.center_at_sweep = None
        self.daily_bullish = None  # 1D EMA50 trend
    
    def set_daily_trend(self, bullish: bool | None):
        """Set 1D trend direction (called from main bot)."""
        self.daily_bullish = bullish
    
    def process_candle(self, df: pd.DataFrame, candle_index: int) -> dict | None:
        """Обработать одну свечу. Вернуть сигнал или None."""
        
        if candle_index < self.lookback:
            return None
        
        current_close = df['close'].iloc[candle_index]
        current_high = df['high'].iloc[candle_index]
        current_low = df['low'].iloc[candle_index]
        
        # Рассчитать center и sweep
        center = find_consolidation_center(df, lookback=self.lookback)
        sweep = detect_sweep(df, center, threshold=self.sweep_threshold)
        
        c = center.iloc[candle_index]
        if pd.isna(c):
            return None
        
        has_bullish = sweep['bullish_sweep'].iloc[candle_index]
        has_bearish = sweep['bearish_sweep'].iloc[candle_index]
        
        # State machine
        if self.state == 0:
            if has_bearish:
                self.state = 1
                self.sweep_direction = 'bearish'
                self.sweep_price = current_high
                self.sweep_index = candle_index
                self.center_at_sweep = c
                return None
            elif has_bullish:
                self.state = 1
                self.sweep_direction = 'bullish'
                self.sweep_price = current_low
                self.sweep_index = candle_index
                self.center_at_sweep = c
                return None
        
        elif self.state == 1:
            # Проверяем return to center
            if abs(current_close - self.center_at_sweep) / self.center_at_sweep < self.center_proximity:
                # ADX фильтр
                skip = False
                if self.adx_enabled:
                    adx, plus_di, minus_di = calculate_adx(df, period=14)
                    current_adx = adx.iloc[candle_index] if not pd.isna(adx.iloc[candle_index]) else 0
                    if current_adx < self.adx_min:
                        skip = True
                    if current_adx > 25:
                        current_plus = plus_di.iloc[candle_index] if not pd.isna(plus_di.iloc[candle_index]) else 0
                        current_minus = minus_di.iloc[candle_index] if not pd.isna(minus_di.iloc[candle_index]) else 0
                        if self.sweep_direction == 'bearish' and current_minus < current_plus:
                            skip = True
                        if self.sweep_direction == 'bullish' and current_plus < current_minus:
                            skip = True
                
                if skip:
                    self.state = 0
                    self.sweep_direction = None
                    return None
                
                # Session filter: only during London+NY (8:00-21:00 UTC)
                if self.session_enabled:
                    ts = df['timestamp'].iloc[candle_index]
                    hour = ts.hour if hasattr(ts, 'hour') else 0
                    if not (8 <= hour <= 21):
                        self.state = 0
                        self.sweep_direction = None
                        return None
                
                # Генерируем сигнал
                entry = self.center_at_sweep
                
                if self.sweep_direction == 'bearish':
                    stop = self.sweep_price * 1.003
                    risk = abs(entry - stop)
                    tp = entry - risk * self.tp_multiplier
                    direction = 'SELL'
                else:
                    stop = self.sweep_price * 0.997
                    risk = abs(stop - entry)
                    tp = entry + risk * self.tp_multiplier
                    direction = 'BUY'
                
                # 1D trend filter: skip against daily trend
                if self.trend_1d_enabled and self.daily_bullish is not None:
                    if direction == 'SELL' and self.daily_bullish:
                        self.state = 0
                        self.sweep_direction = None
                        return None
                    if direction == 'BUY' and not self.daily_bullish:
                        self.state = 0
                        self.sweep_direction = None
                        return None
                
                self.state = 0
                self.sweep_direction = None
                
                return {
                    'direction': direction,
                    'entry': entry,
                    'stop': stop,
                    'tp': tp,
                    'timestamp': str(df['timestamp'].iloc[candle_index]),
                    'confidence': 0.7,
                }
            
            # Timeout
            elif candle_index - self.sweep_index > self.timeout:
                self.state = 0
                self.sweep_direction = None
        
        return None


# ─── TRAILING MANAGER ──────────────────────────────────────────────

class TrailingManager:
    """Управление trailing stop на каждой свече."""
    
    def __init__(self, config: dict, tracker: PositionTracker, executor: OrderExecutor):
        self.config = config
        self.tracker = tracker
        self.executor = executor
        self.enabled = config.get('trailing', {}).get('enabled', True)
        self.breakeven_at = config.get('trailing', {}).get('breakeven_at', 0.5)
        self.trail_activate = config.get('trailing', {}).get('trail_activate', 1.0)
        self.trail_step = config.get('trailing', {}).get('trail_step', 0.5)
    
    def update(self, symbol: str, high: float, low: float, close: float):
        """Обновить trailing для позиции."""
        if not self.enabled:
            return
        
        pos = self.tracker.get_position(symbol)
        if not pos:
            return
        
        risk_unit = pos.risk_unit
        if risk_unit == 0:
            return
        
        # PnL в единицах риска
        if pos.side == "LONG":
            pnl_risk = (high - pos.entry_price) / risk_unit
        else:
            pnl_risk = (pos.entry_price - low) / risk_unit
        
        pos.highest_pnl_risk = max(pos.highest_pnl_risk, pnl_risk)
        
        old_stop = pos.stop_price
        new_stop = old_stop
        
        # Breakeven
        if self.breakeven_at > 0 and pos.highest_pnl_risk >= self.breakeven_at:
            if pos.side == "LONG":
                be_stop = pos.entry_price + pos.entry_price * 0.0005
                new_stop = max(new_stop, be_stop)
            else:
                be_stop = pos.entry_price - pos.entry_price * 0.0005
                new_stop = min(new_stop, be_stop)
        
        # Trailing
        if self.trail_activate > 0 and self.trail_step > 0 and pos.highest_pnl_risk >= self.trail_activate:
            trail_dist = risk_unit * self.trail_step
            if pos.side == "LONG":
                trail_stop = close - trail_dist
                new_stop = max(new_stop, trail_stop)
            else:
                trail_stop = close + trail_dist
                new_stop = min(new_stop, trail_stop)
        
        if new_stop != old_stop:
            # Only update if moved significantly (> 5% of trail distance)
            trail_dist = risk_unit * self.trail_step if self.trail_step > 0 else 0
            min_movement = trail_dist * 0.05 if trail_dist > 0 else (pos.entry_price * 0.001 if pos.entry_price > 0 else 0)
            if abs(new_stop - old_stop) < min_movement:
                return  # too small, skip API call
            
            # Update exchange FIRST, only update tracker on success
            result = self.executor.update_stop_loss(symbol, new_stop)
            if result.success:
                pos.stop_price = new_stop
                self.tracker._save_position(symbol, pos)
                logger.info(f"TRAIL {symbol}: SL {old_stop:.2f} → {new_stop:.2f} "
                           f"(pnl_risk={pos.highest_pnl_risk:.2f})")
            else:
                logger.warning(f"TRAIL FAILED {symbol}: exchange rejected SL {new_stop:.2f}")


# ─── MAIN BOT ──────────────────────────────────────────────────────

class SMCFractalBot:
    """Основной класс бота."""
    
    def __init__(self, config: dict):
        self.config = config
        self.running = False
        
        # Redis store
        self.redis = None
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_db = int(os.getenv("REDIS_DB", "0"))
        redis_pass = os.getenv("REDIS_PASSWORD", None)
        redis_prefix = os.getenv("REDIS_PREFIX", "smc")
        
        try:
            self.redis = RedisStore(
                host=redis_host, port=redis_port, db=redis_db,
                password=redis_pass, prefix=redis_prefix,
            )
            logger.info(f"Redis connected: {redis_host}:{redis_port}/{redis_db}")
        except Exception as e:
            logger.warning(f"Redis unavailable, running without persistence: {e}")
            self.redis = None
        
        # Telegram
        tg_config = config.get('notifications', {}).get('telegram', {})
        self.notifier = TelegramNotifier(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            enabled=tg_config.get('enabled', False),
        )
        
        # Bybit client (demo/testnet/mainnet)
        api_key = os.getenv("BYBIT_API_KEY", "")
        api_secret = os.getenv("BYBIT_API_SECRET", "")
        testnet = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
        demo = os.getenv("BYBIT_DEMO", "true").lower() == "true"
        
        if not api_key or not api_secret:
            logger.error("BYBIT_API_KEY / BYBIT_API_SECRET not set in .env")
            sys.exit(1)
        
        self.client = BybitClient(api_key, api_secret, testnet=testnet, demo=demo)
        self.risk = RiskManager(
            risk_percent=config['risk']['risk_percent'],
            max_drawdown=10.0,
            max_daily_loss=5.0,
            max_consecutive_losses=3,
            max_daily_trades=20,
            commission=config['risk']['commission'],
            slippage=config['risk']['slippage'],
            stop_buffer=config['risk']['stop_buffer'],
            redis_store=self.redis,
            dynamic_risk_enabled=config.get('dynamic_risk', {}).get('enabled', True),
            base_risk=config.get('dynamic_risk', {}).get('base_risk', 1.5),
            min_risk=config.get('dynamic_risk', {}).get('min_risk', 0.75),
            max_risk=config.get('dynamic_risk', {}).get('max_risk', 2.0),
            dd_threshold_1=config.get('dynamic_risk', {}).get('dd_threshold_1', 6.0),
            dd_threshold_2=config.get('dynamic_risk', {}).get('dd_threshold_2', 10.0),
            pf_hot=config.get('dynamic_risk', {}).get('pf_hot', 2.0),
            pf_window=config.get('dynamic_risk', {}).get('pf_window', 20),
        )
        self.executor = OrderExecutor(self.client, self.risk)
        self.tracker = PositionTracker(
            trailing_enabled=config.get('trailing', {}).get('enabled', True),
            breakeven_at=config.get('trailing', {}).get('breakeven_at', 0.5),
            trail_activate=config.get('trailing', {}).get('trail_activate', 1.0),
            trail_step=config.get('trailing', {}).get('trail_step', 0.5),
            redis_store=self.redis,
        )
        self.signal_gen = SignalGenerator(config)
        self.trailing = TrailingManager(config, self.tracker, self.executor)
        
        # Auto-optimizer
        self.optimizer = AutoOptimizer(config, redis_store=self.redis)
        self.start_time = datetime.utcnow()
        self.start_equity = 0.0  # set on first run loop
        # Restore start_equity and start_time from Redis
        try:
            meta = self.redis.load_meta() if self.redis else None
            logger.info(f"LOAD META: {meta}")
            if meta and 'start_equity' in meta and meta['start_equity'] > 0:
                self.start_equity = meta['start_equity']
                logger.info(f"RESTORE start_equity: ${self.start_equity:,.2f}")
            if meta and 'start_time' in meta:
                try:
                    saved_start = datetime.fromisoformat(meta['start_time'])
                    self.start_time = saved_start
                    logger.info(f"RESTORE start_time: {self.start_time.isoformat()}")
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Failed to restore start_equity: {e}")
        self.last_optimize_check = {}  # symbol → datetime
        self.last_trade_close = {}  # symbol → timestamp of last trade close (cooldown)
        
        # Restore cooldown timestamps from Redis
        if self.redis:
            try:
                meta = self.redis.load_meta()
                if meta:
                    # Restore cooldown
                    saved_close = meta.get('last_trade_close', {})
                    now = time.time()
                    for sym, ts in saved_close.items():
                        if now - ts < 4 * 3600:
                            self.last_trade_close[sym] = ts
                            logger.info(f"RESTORE COOLDOWN: {sym} — {int((4*3600 - (now-ts))/60)}min left")
                    # Restore optimize check timestamps
                    saved_opt = meta.get('last_optimize_check', {})
                    for sym, ts_str in saved_opt.items():
                        try:
                            self.last_optimize_check[sym] = datetime.fromisoformat(ts_str)
                        except Exception:
                            pass
                    if saved_opt:
                        logger.info(f"RESTORE OPTIMIZE CHECK: {len(saved_opt)} symbols")
            except Exception as e:
                logger.warning(f"Failed to restore meta: {e}")
        
        # Reporter (daily/weekly reports)
        self.reporter = Reporter(redis_store=self.redis, notifier=self.notifier)
        
        # Dashboard
        self.dashboard = Dashboard(self, port=80)
        
        # Regime state per symbol
        self.regime_state = {}
        
        # Symbols — each with its own config
        self.symbols = []
        self.symbol_configs = {}
        self.locked_configs = set()  # symbols with lock_config=true — optimizer won't touch
        for asset in config.get('assets', []):
            if asset.get('enabled', False):
                sym = asset['symbol']
                self.symbols.append(sym)
                if 'config' in asset:
                    self.symbol_configs[sym] = asset['config']
                else:
                    self.symbol_configs[sym] = {
                        'strategy': config['strategy'],
                        'trailing': config.get('trailing', {}),
                        'filters': config.get('filters', {}),
                    }
                if asset.get('lock_config', False):
                    self.locked_configs.add(sym)
                    logger.info(f"  {sym}: config LOCKED (optimizer disabled)")
        
        # Per-symbol signal generators (must be after self.symbols is defined)
        self.signal_gens = {}
        for sym in self.symbols:
            sym_config = {
                'strategy': self._get_symbol_strategy(sym),
                'filters': self._get_symbol_filters(sym),
            }
            self.signal_gens[sym] = SignalGenerator(sym_config)
        
        # Restore signal states from Redis
        if self.redis:
            self._restore_signal_states()
        
        # Candle data (per symbol)
        self.candle_data = {}
        
        # Graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)
    
    def _shutdown(self, signum, frame):
        """Graceful shutdown."""
        logger.info("Shutdown signal received")
        self.running = False
    
    def _restore_signal_states(self):
        """Restore signal generator states from Redis."""
        if not self.redis:
            return
        for symbol in self.symbols:
            state = self.redis.load_signal_state(symbol)
            if state:
                gen = self.signal_gens.get(symbol)
                if gen:
                    gen.state = state.get('state', 0)
                    gen.sweep_direction = state.get('sweep_direction')
                    gen.sweep_price = state.get('sweep_price')
                    gen.sweep_index = state.get('sweep_index')
                    gen.center_at_sweep = state.get('center_at_sweep')
                logger.info(f"RESTORE SIGNAL STATE: {symbol} state={state.get('state', 0)}")
    
    def _save_signal_state(self, symbol: str):
        """Save signal generator state to Redis."""
        if not self.redis:
            return
        gen = self.signal_gens.get(symbol, self.signal_gen)
        try:
            self.redis.save_signal_state(symbol, {
                'state': gen.state,
                'sweep_direction': gen.sweep_direction,
                'sweep_price': gen.sweep_price,
                'sweep_index': gen.sweep_index,
                'center_at_sweep': gen.center_at_sweep,
            })
        except Exception as e:
            logger.error(f"Redis save signal state failed: {e}")

    def _save_optimize_check(self, symbol: str, dt: datetime):
        """Save last optimize check timestamp to Redis."""
        self.last_optimize_check[symbol] = dt
        if not self.redis:
            return
        try:
            meta = self.redis.load_meta() or {}
            meta['last_optimize_check'] = {
                k: v.isoformat() for k, v in self.last_optimize_check.items()
            }
            self.redis.save_meta(meta)
        except Exception as e:
            logger.error(f"Redis save optimize check failed: {e}")
    
    def _get_symbol_strategy(self, symbol: str) -> dict:
        """Get strategy params for a specific symbol."""
        cfg = self.symbol_configs.get(symbol, {})
        return cfg.get('strategy', self.config['strategy'])
    
    def _get_symbol_trailing(self, symbol: str) -> dict:
        """Get trailing params for a specific symbol."""
        cfg = self.symbol_configs.get(symbol, {})
        return cfg.get('trailing', self.config.get('trailing', {}))
    
    def _get_symbol_filters(self, symbol: str) -> dict:
        """Get filter params for a specific symbol."""
        cfg = self.symbol_configs.get(symbol, {})
        return cfg.get('filters', self.config.get('filters', {}))
    
    def fetch_candles(self, symbol: str, interval: str = "240", limit: int = 200) -> pd.DataFrame:
        """Получить свежие свечи."""
        
        result = self.client.get_klines(symbol, interval=interval, limit=limit)
        list_ = result.get('list', [])
        
        df = pd.DataFrame(list_, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"
        ])
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["timestamp"] = pd.to_datetime(pd.to_numeric(df["timestamp"]), unit="ms")
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df
    
    def sync_position(self, symbol: str):
        """Синхронизировать позицию с биржей."""
        pos = self.client.get_position(symbol)
        tracker_pos = self.tracker.get_position(symbol)
        
        if pos and not tracker_pos:
            # Есть на бирже, нет в tracker — синхронизируем
            side = "LONG" if pos.get('side') == 'Buy' else "SHORT"
            entry = float(pos.get('avgPrice', 0))
            size = float(pos.get('size', 0))
            sl = float(pos.get('stopLoss', 0)) if pos.get('stopLoss') else 0
            tp = float(pos.get('takeProfit', 0)) if pos.get('takeProfit') else 0
            
            if entry > 0 and size > 0:
                self.tracker.open_position(
                    symbol, side, entry, size,
                    stop=sl if sl else entry * (0.995 if side == "LONG" else 1.005),
                    tp=tp if tp else entry * (1.01 if side == "LONG" else 0.99)
                )
                logger.info(f"SYNC: {side} {size} {symbol} @ {entry}")
        
        elif not pos and tracker_pos:
            # Есть в tracker, нет на бирже — закрыли вручную
            self.tracker.close_position(symbol)
            logger.info(f"SYNC: Position {symbol} closed externally")
    
    def run_cycle(self, symbol: str):
        """Один цикл обработки для символа."""
        
        # 1. Получить свечи
        df = self.fetch_candles(symbol, interval="240", limit=200)
        sig_gen = self.signal_gens.get(symbol, self.signal_gen)
        if df.empty or len(df) < sig_gen.lookback + 5:
            return
        
        # 2. Синхронизировать позицию
        self.sync_position(symbol)
        
        # 2b. Update 1D trend if trend filter enabled
        if sig_gen.trend_1d_enabled:
            try:
                df_1d = self.fetch_candles(symbol, interval="D", limit=100)
                if len(df_1d) >= 50:
                    ema50 = df_1d['close'].ewm(span=50).mean().iloc[-1]
                    last_close = df_1d['close'].iloc[-1]
                    sig_gen.set_daily_trend(last_close > ema50)
                else:
                    sig_gen.set_daily_trend(None)
            except Exception as e:
                logger.debug(f"1D trend fetch failed for {symbol}: {e}")
                sig_gen.set_daily_trend(None)
        
        # 2c. Detect market regime (every 4 candles = ~16 hours)
        last_regime = self.regime_state.get(symbol, {})
        last_regime_candle = last_regime.get('last_candle', -999)
        if len(df) - 2 - last_regime_candle >= 4:
            regime = detect_regime(df)
            self.regime_state[symbol] = {
                'regime': regime['regime'],
                'adx': float(regime.get('adx', 0)),
                'strength': float(regime.get('strength', 0)),
                'last_candle': len(df) - 2,
            }
            # Adapt params for regime
            current_params = self._get_symbol_strategy(symbol)
            adapted = self.optimizer.get_regime_params(symbol, regime['regime'], current_params)
            if adapted != current_params:
                self.symbol_configs[symbol]['strategy'].update(adapted)
                # Rebuild signal generator with adapted params, preserving state
                old_gen = self.signal_gens.get(symbol)
                sym_config = {
                    'strategy': self._get_symbol_strategy(symbol),
                    'filters': self._get_symbol_filters(symbol),
                }
                new_gen = SignalGenerator(sym_config)
                if old_gen:
                    new_gen.state = old_gen.state
                    new_gen.sweep_direction = old_gen.sweep_direction
                    new_gen.sweep_price = old_gen.sweep_price
                    new_gen.sweep_index = old_gen.sweep_index
                    new_gen.center_at_sweep = old_gen.center_at_sweep
                self.signal_gens[symbol] = new_gen
                logger.info(f"REGIME ADAPT {symbol}: {regime['regime']} → params updated")
            elif regime['regime'] != 'sideways':
                logger.info(f"REGIME {symbol}: {regime['regime']} "
                          f"(adx={regime['adx']:.1f}, strength={regime['strength']:.2f})")
        
        # 3. Trailing update для открытой позиции
        if self.tracker.has_position(symbol):
            last = df.iloc[-1]
            new_stop = self.trailing.update(symbol, last['high'], last['low'], last['close'])
            
            # Проверить SL/TP
            exit_result = self.tracker.check_exits(
                symbol, last['high'], last['low'], last['close']
            )
            if exit_result:
                logger.info(f"EXIT: {symbol} {exit_result['exit_reason']} PnL={exit_result['pnl']:.2f}")
                self.risk.register_trade(exit_result['pnl'])
                self.executor.close_position(symbol)
                self.last_trade_close[symbol] = time.time()
                # Persist cooldown to Redis
                if self.redis:
                    try:
                        meta = self.redis.load_meta() or {}
                        meta['last_trade_close'] = self.last_trade_close
                        self.redis.save_meta(meta)
                    except Exception:
                        pass
                self.notifier.send_exit(
                    symbol, exit_result['side'], exit_result['entry_price'],
                    exit_result['exit_price'], exit_result['pnl'], exit_result['exit_reason']
                )
        
        # 4. Проверить can_trade
        can, reason = self.risk.can_trade()
        if not can:
            logger.debug(f"Cannot trade {symbol}: {reason}")
            return
        
        # 5. Если нет позиции — ищем сигнал
        if not self.tracker.has_position(symbol):
            # Cooldown: skip if trade closed recently (wait for new 4H candle)
            last_close = self.last_trade_close.get(symbol, 0)
            candle_interval = 4 * 3600  # 4H = 14400 seconds
            if time.time() - last_close < candle_interval:
                logger.debug(f"Cooldown active for {symbol} — skip ({int((candle_interval - (time.time() - last_close))/60)}min left)")
                return  # Skip — wait for new candle
            
            # Используем предпоследнюю свечу (текущая ещё не закрыта)
            candle_idx = len(df) - 2
            sig = sig_gen.process_candle(df, candle_idx)
            self._save_signal_state(symbol)
            
            if sig:
                direction = sig['direction']
                entry = sig['entry']
                stop = sig['stop']
                tp = sig['tp']
                
                logger.info(f"SIGNAL: {direction} {symbol} @ {entry:.2f} SL={stop:.2f} TP={tp:.2f}")
                
                # Вход
                if direction == "BUY":
                    result = self.executor.open_long(symbol, entry, stop, tp)
                else:
                    result = self.executor.open_short(symbol, entry, stop, tp)
                
                if result.success and result.sl_tp_ok:
                    # Use actual filled quantity from exchange
                    size = result.filled_qty
                    
                    if size > 0:
                        tracker_side = "LONG" if direction == "BUY" else "SHORT"
                        self.tracker.open_position(symbol, tracker_side, entry, size, stop, tp)
                        logger.info(f"OPENED: {tracker_side} {size} {symbol} @ {entry:.2f}")
                        self.notifier.send_entry(symbol, direction, entry, stop, tp, size)
                elif result.success and not result.sl_tp_ok:
                    logger.error(f"Position opened but SL/TP failed — emergency closed {symbol}")
                    self.notifier.send_error("SL/TP failed, position auto-closed", f"Entry {symbol}")
                else:
                    logger.error(f"Order failed: {result.message}")
                    self.notifier.send_error(result.message, f"Entry {symbol}")
    
    def _start_telegram_listener(self):
        """Start background thread for Telegram command polling."""
        proxy = os.getenv("TELEGRAM_PROXY", "")
        proxies = {"https": proxy, "http": proxy} if proxy else None
        
        def _poll_loop():
            import requests as req
            offset = 0
            while self.running:
                try:
                    url = f"https://api.telegram.org/bot{self.notifier.bot_token}/getUpdates"
                    resp = req.get(url, params={'offset': offset, 'timeout': 10}, timeout=15, proxies=proxies)
                    if resp.status_code != 200:
                        time.sleep(5)
                        continue
                    data = resp.json()
                    for update in data.get('result', []):
                        offset = update['update_id'] + 1
                        msg = update.get('message', {})
                        text = msg.get('text', '').strip().lower()
                        chat = str(msg.get('chat', {}).get('id', ''))
                        if chat != self.notifier.chat_id:
                            continue
                        logger.info(f"TG CMD: {text} from {chat}")
                        try:
                            self._handle_telegram_command(text)
                        except Exception as e:
                            logger.error(f"TG cmd error: {e}")
                except Exception as e:
                    logger.debug(f"TG poll error: {e}")
                time.sleep(2)
        
        t = threading.Thread(target=_poll_loop, daemon=True, name="tg-commands")
        t.start()
        logger.info("Telegram command listener started")

    def _handle_telegram_command(self, text: str):
        """Handle a Telegram command."""
        balance = self.client.get_wallet_balance()
        equity = float(balance.get('totalEquity', 0)) if balance else 0
        available = float(balance.get('availableToWithdraw', 0)) if balance else 0

        if text == '/report' or text == '/status':
            risk_status = self.risk.get_status()
            report = self.reporter.build_status_report(
                equity, self.tracker.positions, risk_status, self.regime_state)
            self.notifier._send(report)
        elif text == '/daily':
            report = self.reporter.build_daily_report(
                equity, self.tracker.positions, self.regime_state)
            self.notifier._send(report)
        elif text == '/weekly':
            report = self.reporter.build_weekly_report(
                equity, self.tracker.positions, self.regime_state)
            self.notifier._send(report)
        elif text == '/balance':
            positions = self.tracker.positions
            pos_text = ""
            for sym, pos in positions.items():
                e = "+" if pos.side == "LONG" else "-"
                pos_text += f"  {sym}: {pos.side} {pos.size} @ {pos.entry_price:.2f} SL={pos.stop_price:.2f}\n"
            if not pos_text:
                pos_text = "  none\n"
            msg = (
                f"💰 <b>BALANCE</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Equity: <code>${equity:,.2f}</code>\n"
                f"Available: <code>${available:,.2f}</code>\n"
                f"Used: <code>${equity - available:,.2f}</code>\n"
                f"\n📦 <b>Positions ({len(positions)})</b>\n{pos_text}"
                f"━━━━━━━━━━━━━━━━━━"
            )
            self.notifier._send(msg)
        elif text == '/help':
            msg = (
                f"📋 <b>COMMANDS</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"/status — bot status + regime\n"
                f"/balance — equity + positions\n"
                f"/report — same as /status\n"
                f"/daily — daily report\n"
                f"/weekly — weekly report\n"
                f"/help — this message\n"
                f"━━━━━━━━━━━━━━━━━━"
            )
            self.notifier._send(msg)

    def run(self):
        """Основной цикл."""
        logger.info("="*60)
        logger.info("SMC FRACTAL BOT — STARTING")
        logger.info(f"Symbols: {self.symbols}")
        # Telegram start
        testnet = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
        demo = os.getenv("BYBIT_DEMO", "true").lower() == "true"
        mode = 'testnet' if testnet else 'demo' if demo else 'mainnet'
        logger.info(f"Mode: {mode}")
        for sym in self.symbols:
            strat = self._get_symbol_strategy(sym)
            logger.info(f"  {sym}: lb={strat.get('lookback')} sw={strat.get('sweep_threshold')} "
                       f"prox={strat.get('center_proximity')} tp={strat.get('tp_multiplier')}")
        logger.info("="*60)
        
        self.notifier.send_start(self.symbols, testnet)
        
        # Start dashboard
        self.dashboard.run_in_thread()
        
        # Daily report tracker
        last_report_day = datetime.utcnow().date()
        last_week_report_day = None
        
        # Set leverage for all symbols
        for symbol in self.symbols:
            try:
                self.executor.set_leverage(symbol, 10)
            except Exception as e:
                logger.warning(f"Set leverage failed for {symbol}: {e}")
        
        self.running = True
        
        # Start Telegram command listener (after running=True)
        self._start_telegram_listener()
        
        while self.running:
            cycle_start = time.time()
            
            for symbol in self.symbols:
                try:
                    self.run_cycle(symbol)
                except Exception as e:
                    logger.error(f"Cycle error {symbol}: {e}", exc_info=True)
                    self.notifier.send_error(str(e), f"Cycle {symbol}")
            
            # Daily report at 00:10 UTC, weekly on Sunday
            now = datetime.utcnow()
            balance = self.client.get_wallet_balance()
            equity = float(balance.get('totalEquity', 0)) if balance else 0
            
            # Set start_equity on first loop iteration — ONLY once, never overwrite
            if self.start_equity == 0 and equity > 0:
                if self.redis:
                    try:
                        existing = self.redis.load_meta()
                        if existing and 'start_equity' in existing and existing['start_equity'] > 0:
                            self.start_equity = existing['start_equity']
                        else:
                            self.start_equity = equity
                            self.redis.save_meta({'start_equity': equity, 'start_time': self.start_time.isoformat()})
                    except Exception:
                        self.start_equity = equity
                else:
                    self.start_equity = equity
                logger.info(f"START EQUITY: ${self.start_equity:,.2f}")
            
            # Update risk manager with current equity
            self.risk.update_equity(equity)

            if now.date() > last_report_day and now.hour == 0 and now.minute < 10:
                self.reporter.send_daily(equity, self.tracker.positions, self.regime_state)
                self.risk.reset_daily()
                last_report_day = now.date()

            # Weekly report on Sunday
            if now.weekday() == 6 and (last_week_report_day is None or now.date() != last_week_report_day):
                if now.hour == 0 and now.minute < 10:
                    self.reporter.send_weekly(equity, self.tracker.positions, self.regime_state)
                    last_week_report_day = now.date()
            
            # Auto-optimization check (once per day per symbol)
            days_running = (now - self.start_time).total_seconds() / 86400
            for symbol in self.symbols:
                last_check = self.last_optimize_check.get(symbol, datetime.min)
                if (now - last_check).total_seconds() < 86400:  # max once/day
                    continue
                
                try:
                    risk_status = self.risk.get_status()
                    trades = risk_status.get('total_trades', 0)
                    pf = risk_status.get('profit_factor', 2.0)
                    daily_pnl = risk_status.get('daily_pnl', 0)
                    current_equity = risk_status.get('equity', 0)
                    if daily_pnl < 0 and current_equity > 0:
                        daily_dd = abs(daily_pnl) / current_equity
                    else:
                        daily_dd = 0

                    if symbol in self.locked_configs:
                        self._save_optimize_check(symbol, now)
                        continue

                    if self.optimizer.should_optimize(symbol, trades, days_running, pf, daily_dd):
                        df = self.fetch_candles(symbol, interval="240", limit=500)
                        if len(df) > 100:
                            current_params = self._get_symbol_strategy(symbol)
                            current_filters = self._get_symbol_filters(symbol)
                            current_trailing = self._get_symbol_trailing(symbol)
                            
                            result = self.optimizer.optimize(
                                symbol, df, current_params, current_filters, current_trailing
                            )
                            
                            if result:
                                # Apply new config
                                self.symbol_configs[symbol]['strategy'].update(result['strategy'])
                                self.symbol_configs[symbol]['trailing'].update(result['trailing'])
                                # Rebuild signal generator, preserving state
                                old_gen = self.signal_gens.get(symbol)
                                sym_config = {
                                    'strategy': self._get_symbol_strategy(symbol),
                                    'filters': self._get_symbol_filters(symbol),
                                }
                                new_gen = SignalGenerator(sym_config)
                                if old_gen:
                                    new_gen.state = old_gen.state
                                    new_gen.sweep_direction = old_gen.sweep_direction
                                    new_gen.sweep_price = old_gen.sweep_price
                                    new_gen.sweep_index = old_gen.sweep_index
                                    new_gen.center_at_sweep = old_gen.center_at_sweep
                                self.signal_gens[symbol] = new_gen
                                
                                # Log for weekly report
                                self.reporter.log_optimization(symbol, current_params, result['strategy'])
                                
                                logger.info(f"APPLIED NEW CONFIG for {symbol}: {result}")
                                self.notifier.send_error(
                                    f"Auto-optimized {symbol}: PF improvement",
                                    "AUTO-OPTIMIZE"
                                )
                    
                    self._save_optimize_check(symbol, now)
                except Exception as e:
                    logger.error(f"Auto-opt check failed for {symbol}: {e}")
            
            # Ждём до следующей 4H свечи
            now = datetime.utcnow()
            minutes_to_next_4h = (4 - now.hour % 4) * 60 - now.minute
            if minutes_to_next_4h <= 0:
                minutes_to_next_4h = 240
            
            # Проверяем каждые 5 минут
            sleep_time = min(300, minutes_to_next_4h * 60)
            logger.debug(f"Next check in {sleep_time/60:.0f} min")
            
            time.sleep(sleep_time)
        
        # Shutdown
        logger.info("Bot stopped")
        stats = self.tracker.get_stats()
        risk_status = self.risk.get_status()
        logger.info(f"Stats: {stats}")
        logger.info(f"Risk: {risk_status}")
        self.notifier.send_stop()


# ─── ENTRY POINT ───────────────────────────────────────────────────

def main():
    config = load_config()
    bot = SMCFractalBot(config)
    bot.run()


if __name__ == "__main__":
    main()
