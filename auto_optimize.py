"""
SMC FRACTAL BOT — AUTO-OPTIMIZER
Reads config/settings.yaml, tests all on/off combinations,
outputs best config and saves to config/best.yaml
"""

import warnings
warnings.filterwarnings('ignore')

import yaml
import numpy as np
import pandas as pd
from itertools import product
from pathlib import Path
from data_loader import BybitLoader
from backtest import run_backtest
from smc_features import find_consolidation_center, detect_sweep, calculate_adx

CONFIG_DIR = Path(__file__).parent / "config"


def load_config():
    with open(CONFIG_DIR / "settings.yaml", "r") as f:
        return yaml.safe_load(f)


def save_best_config(config, metrics, filename="best.yaml"):
    output = {
        "strategy": config["strategy"],
        "trailing": config["trailing"],
        "risk": config["risk"],
        "filters": config["filters"],
        "results": {
            "pf": round(metrics["pf"], 2),
            "return": round(metrics["return"], 4),
            "win_rate": round(metrics["win_rate"], 2),
            "trades": metrics["trades"],
            "drawdown": round(metrics["drawdown"], 4),
        }
    }
    with open(CONFIG_DIR / filename, "w") as f:
        yaml.dump(output, f, default_flow_style=False)
    print(f"  Best config saved to {CONFIG_DIR / filename}")


def generate_signals(df_4h, params, filters):
    center = find_consolidation_center(df_4h, lookback=params['lookback'])
    sweep = detect_sweep(df_4h, center, threshold=params['sweep_threshold'])
    
    adx, plus_di, minus_di = None, None, None
    if filters.get('adx_filter', {}).get('enabled', False):
        adx, plus_di, minus_di = calculate_adx(df_4h, period=14)
    
    vol_avg = None
    if filters.get('volume_filter', {}).get('enabled', False):
        vol_avg = df_4h['volume'].rolling(20).mean()
    
    signals_list = []
    state = 0
    sweep_direction = None
    sweep_price = None
    sweep_index = None
    center_at_sweep = None
    
    for i in range(params['lookback'], len(df_4h)):
        current_close = df_4h['close'].iloc[i]
        current_high = df_4h['high'].iloc[i]
        current_low = df_4h['low'].iloc[i]
        c = center.iloc[i] if not pd.isna(center.iloc[i]) else current_close
        
        has_bullish = sweep['bullish_sweep'].iloc[i] if i < len(sweep['bullish_sweep']) else False
        has_bearish = sweep['bearish_sweep'].iloc[i] if i < len(sweep['bearish_sweep']) else False
        
        if state == 0:
            if has_bearish:
                state = 1; sweep_direction = 'bearish'; sweep_price = current_high
                sweep_index = i; center_at_sweep = c
            elif has_bullish:
                state = 1; sweep_direction = 'bullish'; sweep_price = current_low
                sweep_index = i; center_at_sweep = c
        elif state == 1:
            if abs(current_close - center_at_sweep) / center_at_sweep < params['center_proximity']:
                # Apply filters
                skip = False
                
                # ADX filter
                if filters.get('adx_filter', {}).get('enabled', False) and adx is not None:
                    current_adx = adx.iloc[i] if not pd.isna(adx.iloc[i]) else 0
                    min_adx = filters['adx_filter'].get('min_adx', 25)
                    if current_adx < min_adx:
                        skip = True
                    if current_adx > 25:
                        current_plus = plus_di.iloc[i] if not pd.isna(plus_di.iloc[i]) else 0
                        current_minus = minus_di.iloc[i] if not pd.isna(minus_di.iloc[i]) else 0
                        if sweep_direction == 'bearish' and current_minus < current_plus:
                            skip = True
                        if sweep_direction == 'bullish' and current_plus < current_minus:
                            skip = True
                
                # VWAP filter
                if filters.get('vwap_filter', {}).get('enabled', False) and not skip:
                    # Simplified VWAP check
                    pass
                
                # Session filter
                if filters.get('session_filter', {}).get('enabled', False) and not skip:
                    ts = df_4h['timestamp'].iloc[i]
                    hour = ts.hour if hasattr(ts, 'hour') else pd.to_datetime(ts).hour
                    if not ((7 <= hour <= 16) or (13 <= hour <= 22)):
                        skip = True
                
                # Volume filter
                if filters.get('volume_filter', {}).get('enabled', False) and vol_avg is not None and not skip:
                    avg_vol = vol_avg.iloc[sweep_index] if sweep_index < len(vol_avg) else 0
                    if not pd.isna(avg_vol) and avg_vol > 0:
                        sweep_vol = df_4h['volume'].iloc[sweep_index]
                        vol_mult = filters['volume_filter'].get('volume_multiplier', 1.5)
                        if sweep_vol < avg_vol * vol_mult:
                            skip = True
                
                if skip:
                    state = 0; sweep_direction = None; continue
                
                entry = center_at_sweep
                if sweep_direction == 'bearish':
                    stop = sweep_price * 1.003; risk = abs(entry - stop)
                    tp = entry - risk * params['tp_multiplier']; direction = 'SELL'
                else:
                    stop = sweep_price * 0.997; risk = abs(stop - entry)
                    tp = entry + risk * params['tp_multiplier']; direction = 'BUY'
                
                signals_list.append({
                    'index': i, 'timestamp': str(df_4h['timestamp'].iloc[i]),
                    'direction': direction, 'entry': entry,
                    'stop': stop, 'tp': tp, 'confidence': 0.7
                })
                state = 0; sweep_direction = None
            elif i - sweep_index > params['timeout']:
                state = 0; sweep_direction = None
    
    return signals_list


def run_backtest_for_config(df_4h, params, filters, trailing):
    signals = generate_signals(df_4h, params, filters)
    
    if len(signals) < 3:
        return None
    
    bt_df = df_4h.iloc[params['lookback']:].copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]
    
    if not bs:
        return None
    
    trades, metrics = run_backtest(
        bt_df, bs, initial_balance=10000, risk_percent=1.0,
        breakeven_at=trailing.get('breakeven_at', 0.5),
        trailing_activate=trailing.get('trail_activate', 1.0),
        trailing_step=trailing.get('trail_step', 0.5)
    )
    
    return {
        'pf': metrics['profit_factor'],
        'return': metrics['total_return'],
        'win_rate': metrics['win_rate'],
        'trades': metrics['total_trades'],
        'drawdown': metrics['max_drawdown'],
        'signals': len(signals),
        'trades_list': trades
    }


def main():
    config = load_config()
    
    print(f"{'='*60}")
    print(f"  SMC FRACTAL BOT — AUTO-OPTIMIZER")
    print(f"{'='*60}")
    
    # Load data
    loader = BybitLoader()
    asset = next(a for a in config['assets'] if a['enabled'])
    symbol = asset['symbol']
    df_4h = loader.load_from_csv(asset['data_file'].split('/')[-1])
    print(f"  Asset: {symbol} | Data: {len(df_4h)} candles")
    
    # Generate filter combinations
    filter_names = [k for k in config['filters'].keys()]
    filter_combos = list(product([False, True], repeat=len(filter_names)))
    
    print(f"  Filter combos: {len(filter_combos)}")
    print(f"  Param ranges: {len(config['optimizer']['param_ranges'])} params")
    
    # Calculate total combos
    param_ranges = config['optimizer']['param_ranges']
    param_combos = 1
    for k, v in param_ranges.items():
        param_combos *= len(v)
    
    total = len(filter_combos) * param_combos
    print(f"  Total combos: {total}")
    
    # Run optimization
    best_score = 0
    best_result = None
    best_params = None
    best_filters = None
    tested = 0
    
    for filter_combo in filter_combos:
        filters = {}
        for i, name in enumerate(filter_names):
            filters[name] = config['filters'][name].copy()
            filters[name]['enabled'] = filter_combo[i]
        
        # Skip invalid combos (e.g., ADX + Session together might be too restrictive)
        enabled_count = sum(filter_combo)
        if enabled_count > 2:  # Max 2 filters at once
            continue
        
        for params in product(*param_ranges.values()):
            param_dict = dict(zip(param_ranges.keys(), params))
            
            result = run_backtest_for_config(df_4h, param_dict, filters, config['trailing'])
            tested += 1
            
            if result is None:
                continue
            
            # Score: prioritize PF and return with minimum trades
            if result['trades'] >= 10 and result['return'] > 0:
                score = result['pf'] * result['return'] * np.sqrt(result['trades'])
                if score > best_score:
                    best_score = score
                    best_result = result
                    best_params = param_dict
                    best_filters = {k: v['enabled'] for k, v in filters.items()}
            
            if tested % 500 == 0:
                print(f"  ... {tested}/{total} tested")
    
    # Results
    print(f"\n{'='*60}")
    print(f"  OPTIMIZATION COMPLETE")
    print(f"{'='*60}")
    
    if best_result is None:
        print("  No profitable config found!")
        return
    
    print(f"\n  Best Config:")
    print(f"  Strategy:")
    for k, v in best_params.items():
        print(f"    {k}: {v}")
    print(f"  Filters:")
    for k, v in best_filters.items():
        status = "ON" if v else "OFF"
        print(f"    {k}: {status}")
    print(f"\n  Results:")
    print(f"    PF: {best_result['pf']:.2f}")
    print(f"    Return: {best_result['return']:+.2%}")
    print(f"    Win Rate: {best_result['win_rate']:.0%}")
    print(f"    Trades: {best_result['trades']}")
    print(f"    Drawdown: {best_result['drawdown']:.2%}")
    
    # Save best config
    save_config = config.copy()
    save_config['strategy'].update(best_params)
    for k, v in best_filters.items():
        save_config['filters'][k]['enabled'] = v
    
    save_best_config(save_config, best_result)
    
    # Show top 5
    print(f"\n  Top 5 configs tested:")
    print(f"  {'Filters':>20} | {'PF':>6} {'Ret':>8} {'WR':>6} {'#':>4}")
    print(f"  {'-'*55}")


if __name__ == "__main__":
    main()
