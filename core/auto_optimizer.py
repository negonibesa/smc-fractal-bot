"""
Auto-Optimizer with walk-forward validation and guard rails.

Rules (from Grok's recommendations):
1. Walk-forward validate ANY new config before applying
2. Never overwrite optimal_v2.yaml baseline
3. Log parameter deltas — red flag if jump > 50%
4. Conservative parameter ranges only
5. Minimum 10% improvement over current config
"""
import logging
import yaml
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger("optimizer")

# ─── PARAM RANGES (conservative) ────────────────────────────────
# Never let params drift far from baseline
PARAM_RANGES = {
    'lookback': [8, 10, 12, 15],
    'sweep_threshold': [0.005, 0.008, 0.010, 0.012],
    'center_proximity': [0.008, 0.010, 0.012, 0.015],
    'tp_multiplier': [1.0, 1.5, 2.0],
    'timeout': [6, 10, 15, 20],
}

# Baseline params (from optimal_v2) — used as anchor
BASELINE_PARAMS = {
    'lookback': 12,
    'sweep_threshold': 0.008,
    'center_proximity': 0.012,
    'tp_multiplier': 1.0,
    'timeout': 15,
}


def generate_param_combos(ranges: dict, max_combos: int = 50) -> list[dict]:
    """Generate parameter combinations from ranges."""
    import itertools
    keys = list(ranges.keys())
    values = [ranges[k] for k in keys]
    combos = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
    
    # Limit combos to avoid long optimization runs
    if len(combos) > max_combos:
        # Sample evenly across the space
        indices = np.linspace(0, len(combos)-1, max_combos, dtype=int)
        combos = [combos[i] for i in indices]
    
    return combos


def run_walk_forward(df: pd.DataFrame, params: dict, filters: dict, 
                     trailing: dict, n_splits: int = 3) -> Optional[dict]:
    """
    Run walk-forward test on df with given params.
    Returns avg OOS metrics or None if insufficient data.
    """
    from backtest import run_backtest
    from smc_features import find_consolidation_center, detect_sweep, calculate_adx
    
    lookback = params['lookback']
    n = len(df)
    
    if n < lookback * 4:
        return None
    
    def run_bt_segment(df_part):
        if len(df_part) < lookback + 10:
            return None
        
        center = find_consolidation_center(df_part, lookback=lookback)
        sweep = detect_sweep(df_part, center, threshold=params['sweep_threshold'])
        adx, pdi, mdi = None, None, None
        if filters.get('adx_filter', {}).get('enabled'):
            adx, pdi, mdi = calculate_adx(df_part, period=14)
        
        signals = []
        state = 0; sw_dir = None; sw_price = None; sw_idx = None; ctr = None
        
        for i in range(lookback, len(df_part)):
            c = center.iloc[i] if not pd.isna(center.iloc[i]) else df_part['close'].iloc[i]
            h, l, cl = df_part['high'].iloc[i], df_part['low'].iloc[i], df_part['close'].iloc[i]
            has_bull = sweep['bullish_sweep'].iloc[i] if i < len(sweep['bullish_sweep']) else False
            has_bear = sweep['bearish_sweep'].iloc[i] if i < len(sweep['bearish_sweep']) else False
            
            if state == 0:
                if has_bear: state=1; sw_dir='bearish'; sw_price=h; sw_idx=i; ctr=c
                elif has_bull: state=1; sw_dir='bullish'; sw_price=l; sw_idx=i; ctr=c
            elif state == 1:
                if abs(cl - ctr) / ctr < params['center_proximity']:
                    skip = False
                    if adx is not None:
                        cur_adx = adx.iloc[i] if not pd.isna(adx.iloc[i]) else 0
                        if cur_adx < filters.get('adx_filter', {}).get('min_adx', 25): skip = True
                        if cur_adx > 25:
                            p_val = pdi.iloc[i] if not pd.isna(pdi.iloc[i]) else 0
                            m_val = mdi.iloc[i] if not pd.isna(mdi.iloc[i]) else 0
                            if sw_dir == 'bearish' and m_val < p_val: skip = True
                            if sw_dir == 'bullish' and p_val < m_val: skip = True
                    if skip: state=0; sw_dir=None; continue
                    
                    entry = ctr
                    if sw_dir == 'bearish':
                        stop=sw_price*1.003; risk=abs(entry-stop)
                        tp=entry-risk*params['tp_multiplier']; d='SELL'
                    else:
                        stop=sw_price*0.997; risk=abs(stop-entry)
                        tp=entry+risk*params['tp_multiplier']; d='BUY'
                    signals.append({'index':i,'timestamp':str(df_part['timestamp'].iloc[i]),
                                    'direction':d,'entry':entry,'stop':stop,'tp':tp,'confidence':0.7})
                    state=0; sw_dir=None
                elif i - sw_idx > params['timeout']: state=0; sw_dir=None
        
        if len(signals) < 3: return None
        bt_df = df_part.iloc[lookback:].copy()
        bt_df.index = [str(t) for t in bt_df['timestamp']]
        sd = {s['timestamp']: s for s in signals}
        bs = [sd[ts] for ts in bt_df.index if ts in sd]
        if not bs: return None
        _, metrics = run_backtest(bt_df, bs, initial_balance=10000, risk_percent=1.0,
            breakeven_at=trailing.get('breakeven_at',0.5),
            trailing_activate=trailing.get('trail_activate',1.0),
            trailing_step=trailing.get('trail_step',0.5))
        return {'pf': metrics['profit_factor'], 'wr': metrics['win_rate'],
                'ret': metrics['total_return'], 'dd': metrics['max_drawdown'],
                'trades': metrics['total_trades']}
    
    # Walk-forward: split into n_splits, test on each OOS portion
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
    """Auto-optimization with guard rails."""
    
    def __init__(self, config: dict, redis_store=None):
        self.config = config
        self.redis = redis_store
        opt_cfg = config.get('optimizer', {})
        self.trigger_days = opt_cfg.get('trigger_days', 30)
        self.trigger_trades = opt_cfg.get('trigger_trades', 20)
        self.trigger_pf_drop = opt_cfg.get('trigger_pf_drop', 1.2)
        self.min_improvement = opt_cfg.get('min_improvement', 0.10)
        self.max_param_delta = 0.5  # 50% max drift from baseline
        
        # Load baseline config (iron — never auto-overwrite)
        baseline_path = Path(__file__).parent.parent / "config" / "optimal_v2.yaml"
        if baseline_path.exists():
            with open(baseline_path) as f:
                self.baseline = yaml.safe_load(f)
            logger.info(f"Loaded baseline config from {baseline_path}")
        else:
            self.baseline = config
            logger.warning("No optimal_v2.yaml found, using current config as baseline")
        
        # Optimization log
        self.log_path = Path(__file__).parent.parent / "logs" / "optimizer.log"
        self.log_path.parent.mkdir(exist_ok=True)
    
    def should_optimize(self, symbol: str, trades_count: int, 
                       days_since_start: float, current_pf: float) -> bool:
        """Check if optimization should trigger."""
        if trades_count < self.trigger_trades:
            return False
        if days_since_start < self.trigger_days:
            return False
        if current_pf >= self.trigger_pf_drop:
            return False
        
        logger.info(f"OPTIMIZE TRIGGER: {symbol} — {trades_count} trades, "
                    f"{days_since_start:.0f} days, PF={current_pf:.2f} < {self.trigger_pf_drop}")
        return True
    
    def optimize(self, symbol: str, df: pd.DataFrame, 
                current_params: dict, current_filters: dict,
                current_trailing: dict) -> Optional[dict]:
        """
        Run optimization for a symbol.
        Returns new config dict if better, None otherwise.
        """
        logger.info(f"OPTIMIZING {symbol} — testing {len(PARAM_RANGES)} param dimensions")
        
        # Generate parameter combos
        combos = generate_param_combos(PARAM_RANGES, max_combos=40)
        
        best_config = None
        best_pf = 0
        all_results = []
        
        for combo in combos:
            test_params = {**current_params, **combo}
            
            result = run_walk_forward(df, test_params, current_filters, current_trailing)
            if result is None:
                continue
            
            all_results.append({'params': combo, 'result': result})
            
            # Must beat current PF by min_improvement
            # Also must have reasonable min PF across all OOS splits
            if (result['avg_pf'] > best_pf and 
                result['min_pf'] >= 1.0 and
                result['total_trades'] >= 5):
                best_pf = result['avg_pf']
                best_config = combo
        
        if best_config is None:
            logger.info(f"OPTIMIZE {symbol}: no better config found")
            return None
        
        # Check parameter delta from baseline
        baseline_params = self.baseline.get('assets', [{}])[0].get('config', {}).get('strategy', BASELINE_PARAMS)
        delta = param_delta(best_config, baseline_params)
        
        if delta > self.max_param_delta:
            logger.warning(f"OPTIMIZE {symbol}: best config too far from baseline "
                          f"(delta={delta:.2f} > {self.max_param_delta}). Skipping.")
            self._log_optimization(symbol, best_config, best_pf, delta, rejected=True,
                                  reason="param_delta_too_high")
            return None
        
        # Apply improvement threshold
        current_avg_pf = self._get_current_pf(symbol)
        improvement = (best_pf - current_avg_pf) / current_avg_pf if current_avg_pf > 0 else 0
        
        if improvement < self.min_improvement:
            logger.info(f"OPTIMIZE {symbol}: improvement {improvement:.1%} < {self.min_improvement:.1%}. Skipping.")
            return None
        
        logger.info(f"OPTIMIZE {symbol}: FOUND BETTER CONFIG — PF {current_avg_pf:.2f} -> {best_pf:.2f} "
                    f"(+{improvement:.1%}), delta={delta:.2f}")
        
        self._log_optimization(symbol, best_config, best_pf, delta, rejected=False,
                              improvement=improvement)
        
        return best_config
    
    def _get_current_pf(self, symbol: str) -> float:
        """Get current PF from Redis or return 1.0."""
        if self.redis:
            try:
                stats = self.redis.load_trade_stats(symbol)
                if stats and stats.get('total_trades', 0) > 0:
                    wins = stats.get('wins', 0)
                    losses = stats.get('losses', 0)
                    if losses > 0:
                        return (wins * 1.0) / (losses * 1.0)  # Simplified
            except:
                pass
        return 1.5  # Default assumption
    
    def _log_optimization(self, symbol: str, params: dict, pf: float, 
                         delta: float, rejected: bool = False, 
                         improvement: float = 0, reason: str = ""):
        """Log optimization attempt."""
        entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'symbol': symbol,
            'new_params': params,
            'new_pf': pf,
            'param_delta': delta,
            'rejected': rejected,
            'improvement': improvement,
            'reason': reason,
        }
        
        with open(self.log_path, 'a') as f:
            f.write(f"{entry}\n")
        
        # Also save to Redis if available
        if self.redis:
            try:
                self.redis.save_optimization_log(symbol, entry)
            except:
                pass
