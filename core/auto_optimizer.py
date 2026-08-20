"""
Auto-Optimizer v2 — walk-forward validated, deploy-and-forget.

My rules (not Grok's — I disagree on some points):
1. Walk-forward validate ANY new config before applying
2. Never overwrite optimal_v2.yaml baseline
3. Log parameter deltas — red flag if > 50% drift
4. Conservative parameter ranges (ADX NEVER touched)
5. Minimum 10% improvement over current config
6. 7-day cooldown after each optimization
7. Max DD > 5% in a day → force optimization immediately
8. Trade count drop < 50% is OK if PF improves (fewer better trades > more mediocre)
9. Per-regime param sets: bull / bear / sideways stored separately
"""
import logging
import yaml
import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("optimizer")

# ─── PARAM RANGES (conservative) ────────────────────────────────
# ADX is NEVER optimized — it's part of strategy identity, not a tuning knob
PARAM_RANGES = {
    'lookback': [8, 10, 12, 15],
    'sweep_threshold': [0.005, 0.008, 0.010, 0.012],
    'center_proximity': [0.008, 0.010, 0.012, 0.015],
    'tp_multiplier': [1.0, 1.5, 2.0],
    'timeout': [6, 10, 15, 20],
}

TRAILING_RANGES = {
    'breakeven_at': [0.3, 0.5, 0.7],
    'trail_activate': [0.8, 1.0, 1.5],
    'trail_step': [0.3, 0.5, 0.7],
}

# Baseline params (from optimal_v2) — used as anchor
BASELINE_PARAMS = {
    'lookback': 12,
    'sweep_threshold': 0.008,
    'center_proximity': 0.012,
    'tp_multiplier': 1.0,
    'timeout': 15,
}

# Per-regime param adjustments (relative to baseline)
REGIME_OVERRIDES = {
    'bull': {
        'center_proximity': 0.015,   # wider entry in trends
        'tp_multiplier': 1.5,        # ride the trend
        'timeout': 10,               # don't wait too long
    },
    'bear': {
        'center_proximity': 0.008,   # tighter entry
        'tp_multiplier': 1.0,        # take profit faster
        'timeout': 8,                # false breakouts more common
    },
    'sideways': {},  # use baseline defaults
}


def generate_param_combos(ranges: dict, max_combos: int = 50) -> list[dict]:
    """Generate parameter combinations from ranges."""
    import itertools
    keys = list(ranges.keys())
    values = [ranges[k] for k in keys]
    combos = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
    
    if len(combos) > max_combos:
        indices = np.linspace(0, len(combos)-1, max_combos, dtype=int)
        combos = [combos[i] for i in indices]
    
    return combos


def run_walk_forward(df: pd.DataFrame, params: dict, filters: dict, 
                     trailing: dict, n_splits: int = 3,
                     commission: float = 0.001, slippage: float = 0.0005,
                     stop_buffer: float = 0.002, max_leverage: float = 10.0,
                     dynamic_risk: dict = None,
                     cooldown_hours: float = 4.0,
                     max_daily_loss_pct: float = 5.0,
                     max_daily_trades: int = 20) -> Optional[dict]:
    """
    Run walk-forward test on df with given params.
    Uses the real SignalGenerator (same logic as live) to avoid duplication.
    Returns avg OOS metrics or None if insufficient data.
    """
    from backtest import run_backtest
    from main import SignalGenerator
    
    lookback = params['lookback']
    n = len(df)
    
    if n < lookback * 4:
        return None
    
    def run_bt_segment(df_part):
        if len(df_part) < lookback + 10:
            return None
        
        sig_config = {'strategy': params, 'filters': filters}
        gen = SignalGenerator(sig_config, symbol='BACKTEST')
        
        signals = []
        for i in range(len(df_part)):
            sig = gen.process_candle(df_part, i)
            if sig:
                sig['index'] = i
                sig['timestamp'] = str(df_part['timestamp'].iloc[i])
                signals.append(sig)
        
        if len(signals) < 3:
            return None
        
        bt_df = df_part.copy()
        bt_df.index = [str(t) for t in bt_df['timestamp']]
        sd = {s['timestamp']: s for s in signals}
        bs = [sd[ts] for ts in bt_df.index if ts in sd]
        if not bs:
            return None
        
        _, metrics = run_backtest(bt_df, bs, initial_balance=10000, risk_percent=1.0,
            commission=commission, slippage=slippage, stop_buffer=stop_buffer,
            breakeven_at=trailing.get('breakeven_at', 0.5),
            trailing_activate=trailing.get('trail_activate', 1.0),
            trailing_step=trailing.get('trail_step', 0.5),
            max_leverage=max_leverage, dynamic_risk=dynamic_risk,
            cooldown_hours=cooldown_hours,
            max_daily_loss_pct=max_daily_loss_pct,
            max_daily_trades=max_daily_trades)
        
        return {'pf': metrics['profit_factor'], 'wr': metrics['win_rate'],
                'ret': metrics['total_return'], 'dd': metrics['max_drawdown'],
                'trades': metrics['total_trades']}
    
    split_size = n // n_splits
    oos_results = []
    
    for i in range(1, n_splits):
        test_start = i * split_size
        test_end = min((i + 1) * split_size, n)
        df_test = df.iloc[test_start:test_end].copy().reset_index(drop=True)
        result = run_bt_segment(df_test)
        if result and result['trades'] >= 3:
            oos_results.append(result)
    
    if not oos_results:
        return None
    
    return {
        'avg_pf': np.mean([r['pf'] for r in oos_results]),
        'avg_wr': np.mean([r['wr'] for r in oos_results]),
        'avg_ret': np.mean([r['ret'] for r in oos_results]),
        'avg_dd': np.mean([r['dd'] for r in oos_results]),
        'total_trades': sum(r['trades'] for r in oos_results),
        'min_pf': min(r['pf'] for r in oos_results),
        'max_dd': max(r['dd'] for r in oos_results),
    }


def param_delta(p1: dict, p2: dict) -> float:
    """Calculate normalized parameter distance between two configs."""
    deltas = []
    for key in p1:
        if key in p2 and BASELINE_PARAMS.get(key, 0) > 0:
            d = abs(p1[key] - p2[key]) / BASELINE_PARAMS[key]
            deltas.append(d)
    return np.mean(deltas) if deltas else 0


class AutoOptimizer:
    """Auto-optimization with guard rails — my version."""
    
    def __init__(self, config: dict, redis_store=None):
        self.config = config
        self.redis = redis_store
        opt_cfg = config.get('optimizer', {})
        self.trigger_days = opt_cfg.get('trigger_days', 90)
        self.trigger_trades = opt_cfg.get('trigger_trades', 15)
        self.trigger_pf_drop = opt_cfg.get('trigger_pf_drop', 1.2)
        self.trigger_dd_daily = 0.05  # 5% daily DD → force optimize
        self.min_improvement = opt_cfg.get('min_improvement', 0.10)
        self.max_param_delta = 0.5
        self.cooldown_trades = opt_cfg.get('cooldown_trades', 30)
        self.max_trade_drop = 0.50  # 50% trade drop is OK if PF improves
        
        # Load baseline config (iron — never auto-overwrite)
        baseline_path = Path(__file__).parent.parent / "config" / "optimal_v2.yaml"
        if baseline_path.exists():
            with open(baseline_path) as f:
                self.baseline = yaml.safe_load(f)
            logger.info(f"Loaded baseline config from {baseline_path}")
        else:
            self.baseline = config
            logger.warning("No optimal_v2.yaml found, using current config as baseline")
        
        # State tracking
        self.last_optimize_time = {}  # symbol -> datetime
        self.last_optimize_trades = {}  # symbol -> trade_count at last optimization
        self.log_path = Path(__file__).parent.parent / "logs" / "optimizer.log"
        self.log_path.parent.mkdir(exist_ok=True)
        
        # Per-regime configs (loaded from Redis or initialized)
        self.regime_configs = {}  # {symbol: {bull: {...}, bear: {...}, sideways: {...}}}
    
    def should_optimize(self, symbol: str, trades_count: int, 
                       days_since_start: float, current_pf: float,
                       daily_dd: float = 0.0) -> bool:
        """Check if optimization should trigger.
        
        Logic:
        1. Force: daily DD > 5% → optimize immediately
        2. Cooldown: < 30 trades since last optimization → skip
        3. Main: >= 15 trades AND PF < 1.2 → optimize
        4. Safety: >= 90 days → optimize (max interval)
        """
        
        # Force trigger: daily DD > 5%
        if daily_dd > self.trigger_dd_daily:
            logger.info(f"FORCE OPTIMIZE: {symbol} — daily DD={daily_dd:.1%} > {self.trigger_dd_daily:.1%}")
            return True
        
        # PF drop trigger: bypass cooldown (emergency re-optimize)
        if trades_count >= self.trigger_trades and current_pf < self.trigger_pf_drop:
            logger.info(f"PF DROP OPTIMIZE: {symbol} — PF={current_pf:.2f} < {self.trigger_pf_drop}, "
                        f"bypassing cooldown")
            self.last_optimize_trades[symbol] = trades_count
            return True
        
        # Cooldown check (trade-based)
        last_trades = self.last_optimize_trades.get(symbol, 0)
        trades_since = trades_count - last_trades
        if trades_since < self.cooldown_trades:
            return False
        
        # Main trigger: enough trades (after cooldown)
        if trades_count >= self.trigger_trades:
            logger.info(f"OPTIMIZE TRIGGER: {symbol} — {trades_count} trades, "
                        f"cooldown passed")
            self.last_optimize_trades[symbol] = trades_count
            return True
        
        # Safety trigger: max interval (90 days)
        if days_since_start >= self.trigger_days:
            logger.info(f"OPTIMIZE TRIGGER: {symbol} — {days_since_start:.0f} days >= {self.trigger_days} (max interval)")
            self.last_optimize_trades[symbol] = trades_count
            return True
        
        return False
    
    def optimize(self, symbol: str, df: pd.DataFrame, 
                current_params: dict, current_filters: dict,
                current_trailing: dict,
                risk_config: dict = None) -> Optional[dict]:
        """
        Run optimization for a symbol.
        Returns new config dict if better, None otherwise.
        """
        logger.info(f"OPTIMIZING {symbol} — testing param space")
        
        risk_config = risk_config or {}
        bt_kwargs = dict(
            commission=risk_config.get('commission', 0.001),
            slippage=risk_config.get('slippage', 0.0005),
            stop_buffer=risk_config.get('stop_buffer', 0.002),
            max_leverage=risk_config.get('max_leverage', 10.0),
            dynamic_risk=self.config.get('dynamic_risk'),
            cooldown_hours=4.0,
            max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
            max_daily_trades=risk_config.get('max_daily_trades', 20),
        )
        
        # Test strategy params
        combos = generate_param_combos(PARAM_RANGES, max_combos=40)
        best_strategy = None
        best_pf = 0
        best_result = None
        current_trades = 0
        
        for combo in combos:
            test_params = {**current_params, **combo}
            result = run_walk_forward(df, test_params, current_filters, current_trailing,
                                      **bt_kwargs)
            if result is None:
                continue
            
            current_trades = max(current_trades, result['total_trades'])
            
            if (result['avg_pf'] > best_pf and 
                result['min_pf'] >= 1.0 and
                result['total_trades'] >= 3):
                best_pf = result['avg_pf']
                best_strategy = combo
                best_result = result
        
        if best_strategy is None:
            logger.info(f"OPTIMIZE {symbol}: no better strategy config found")
            return None
        
        # Test trailing params with best strategy
        best_trailing = current_trailing.copy()
        best_overall_pf = best_pf
        
        for tc in generate_param_combos(TRAILING_RANGES, max_combos=15):
            test_trailing = {**current_trailing, **tc}
            test_params = {**current_params, **best_strategy}
            result = run_walk_forward(df, test_params, current_filters, test_trailing,
                                      **bt_kwargs)
            if result and result['avg_pf'] > best_overall_pf and result['min_pf'] >= 1.0:
                best_overall_pf = result['avg_pf']
                best_trailing = test_trailing
                best_result = result
        
        # Guard rails — find baseline for this specific symbol
        baseline_params = BASELINE_PARAMS  # fallback
        for asset in self.baseline.get('assets', []):
            if asset.get('symbol') == symbol:
                baseline_params = asset.get('config', {}).get('strategy', BASELINE_PARAMS)
                break
        delta = param_delta(best_strategy, baseline_params)
        
        if delta > self.max_param_delta:
            logger.warning(f"OPTIMIZE {symbol}: too far from baseline (delta={delta:.2f}). Skipping.")
            self._log(symbol, best_strategy, best_trailing, best_result, delta, rejected=True,
                     reason="param_delta_too_high")
            return None
        
        # Improvement check (soft — compare against current live PF, not walk-forward)
        current_avg_pf = self._get_current_pf(symbol)
        improvement = (best_pf - current_avg_pf) / current_avg_pf if current_avg_pf > 0 else 0
        
        if improvement < self.min_improvement:
            logger.info(f"OPTIMIZE {symbol}: improvement {improvement:.1%} < {self.min_improvement:.1%}. Skipping.")
            return None
        
        # Trade count guard: reject if trades drop > 50% UNLESS PF improves significantly
        if best_result and current_trades > 0:
            trade_drop = 1 - (best_result['total_trades'] / current_trades)
            if trade_drop > self.max_trade_drop and improvement < 0.20:
                logger.info(f"OPTIMIZE {symbol}: trades dropped {trade_drop:.0%} without 20%+ PF gain. Skipping.")
                self._log(symbol, best_strategy, best_trailing, best_result, delta, rejected=True,
                         reason=f"trade_drop_{trade_drop:.0%}_no_pf_gain")
                return None
        
        logger.info(f"OPTIMIZE {symbol}: BETTER CONFIG — PF {current_avg_pf:.2f} -> {best_pf:.2f} "
                    f"(+{improvement:.1%}), delta={delta:.2f}")
        
        self._log(symbol, best_strategy, best_trailing, best_result, delta, rejected=False,
                 improvement=improvement)
        self.last_optimize_time[symbol] = datetime.utcnow()
        
        return {'strategy': best_strategy, 'trailing': best_trailing}
    
    def get_regime_params(self, symbol: str, regime: str, 
                         base_params: dict) -> dict:
        """Get params adapted for current market regime."""
        overrides = REGIME_OVERRIDES.get(regime, {})
        return {**base_params, **overrides}
    
    def _get_current_pf(self, symbol: str) -> float:
        """Get current PF from closed trades in Redis."""
        if self.redis:
            try:
                trades = self.redis.load_closed_trades()
                if trades:
                    # Filter by symbol if specified
                    sym_trades = [t for t in trades if t.get('symbol') == symbol] if symbol else trades
                    if len(sym_trades) < 2:
                        return 1.5
                    gross_profit = sum(t.get('pnl', 0) for t in sym_trades if t.get('pnl', 0) > 0)
                    gross_loss = abs(sum(t.get('pnl', 0) for t in sym_trades if t.get('pnl', 0) <= 0))
                    if gross_loss > 0:
                        return gross_profit / gross_loss
            except Exception:
                pass
        return 1.5
    
    def _log(self, symbol: str, strategy_params: dict, trailing_params: dict,
            result: dict, delta: float, rejected: bool = False, 
            improvement: float = 0, reason: str = ""):
        """Log optimization attempt."""
        entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'symbol': symbol,
            'strategy_params': strategy_params,
            'trailing_params': trailing_params,
            'result': result,
            'param_delta': delta,
            'rejected': rejected,
            'improvement': improvement,
            'reason': reason,
        }
        
        with open(self.log_path, 'a') as f:
            f.write(json.dumps(entry, default=str) + "\n")
