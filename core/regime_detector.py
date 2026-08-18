"""
Market Regime Detector — classifies market as bull/bear/sideways.

Used to adapt strategy parameters per regime:
- Bull: tighter stops, faster TP
- Bear: wider stops, shorter timeout (more false breakouts)
- Sideways: standard params (current optimal)
"""
import logging
import numpy as np
import pandas as pd
from typing import Optional

logger = logging.getLogger("regime")


def detect_regime(df: pd.DataFrame, lookback: int = 50) -> dict:
    """
    Detect market regime from recent price action.
    
    Returns:
        {
            'regime': 'bull' | 'bear' | 'sideways',
            'strength': 0.0-1.0 (how strong the trend is),
            'adx': float,
            'ema_cross': 'bull' | 'bear' | 'none',
            'atr_percentile': float (0-1),
        }
    """
    if len(df) < lookback:
        return {'regime': 'sideways', 'strength': 0.0, 'adx': 0, 
                'ema_cross': 'none', 'atr_percentile': 0.5}
    
    recent = df.iloc[-lookback:].copy()
    
    # --- EMA Cross (20/50) ---
    ema20 = recent['close'].ewm(span=20).mean()
    ema50 = recent['close'].ewm(span=50).mean()
    
    ema_cross = 'none'
    if ema20.iloc[-1] > ema50.iloc[-1] and ema20.iloc[-5] <= ema50.iloc[-5]:
        ema_cross = 'bull'
    elif ema20.iloc[-1] < ema50.iloc[-1] and ema20.iloc[-5] >= ema50.iloc[-5]:
        ema_cross = 'bear'
    elif ema20.iloc[-1] > ema50.iloc[-1]:
        ema_cross = 'bull'
    elif ema20.iloc[-1] < ema50.iloc[-1]:
        ema_cross = 'bear'
    
    # --- ADX (trend strength) ---
    from smc_features import calculate_adx
    adx, plus_di, minus_di = calculate_adx(df, period=14)
    current_adx = adx.iloc[-1] if not pd.isna(adx.iloc[-1]) else 0
    
    # --- ATR percentile (volatility regime) ---
    from smc_features import calculate_atr
    atr = calculate_atr(df, period=14)
    atr_series = atr.dropna()
    if len(atr_series) > 0:
        current_atr = atr.iloc[-1]
        atr_percentile = (atr_series < current_atr).mean()
    else:
        atr_percentile = 0.5
    
    # --- Price position relative to range ---
    high_50 = recent['high'].max()
    low_50 = recent['low'].min()
    price_range = high_50 - low_50
    if price_range > 0:
        price_position = (recent['close'].iloc[-1] - low_50) / price_range
    else:
        price_position = 0.5
    
    # --- Classification ---
    trend_strength = current_adx / 100.0  # normalize 0-1
    
    if current_adx > 25:
        if ema_cross == 'bull' and price_position > 0.6:
            regime = 'bull'
        elif ema_cross == 'bear' and price_position < 0.4:
            regime = 'bear'
        else:
            regime = 'sideways'
    else:
        regime = 'sideways'
    
    return {
        'regime': regime,
        'strength': trend_strength,
        'adx': current_adx,
        'ema_cross': ema_cross,
        'atr_percentile': atr_percentile,
        'price_position': price_position,
    }


def adapt_params_for_regime(base_params: dict, regime_info: dict) -> dict:
    """
    Adapt strategy parameters based on detected regime.
    
    Returns adapted params dict.
    """
    params = base_params.copy()
    regime = regime_info['regime']
    atr_pct = regime_info.get('atr_percentile', 0.5)
    
    if regime == 'bull':
        # In uptrend: allow wider entry, faster TP
        params['center_proximity'] = min(params['center_proximity'] * 1.2, 0.02)
        params['tp_multiplier'] = max(params['tp_multiplier'] * 0.9, 0.8)
        logger.debug(f"REGIME BULL: prox={params['center_proximity']:.3f} tp={params['tp_multiplier']:.1f}")
    
    elif regime == 'bear':
        # In downtrend: tighter entry, wider stops
        params['center_proximity'] = max(params['center_proximity'] * 0.8, 0.005)
        params['timeout'] = max(params['timeout'] - 3, 4)
        logger.debug(f"REGIME BEAR: prox={params['center_proximity']:.3f} timeout={params['timeout']}")
    
    # High volatility adjustment
    if atr_pct > 0.8:
        # Very high volatility: be more conservative
        params['tp_multiplier'] = min(params['tp_multiplier'] * 1.2, 3.0)
        params['timeout'] = max(params['timeout'] - 2, 4)
        logger.debug(f"HIGH VOL: tp={params['tp_multiplier']:.1f} timeout={params['timeout']}")
    
    elif atr_pct < 0.2:
        # Low volatility: tighter targets
        params['tp_multiplier'] = max(params['tp_multiplier'] * 0.8, 0.8)
        logger.debug(f"LOW VOL: tp={params['tp_multiplier']:.1f}")
    
    return params
