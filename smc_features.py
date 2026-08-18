import numpy as np
import pandas as pd

def detect_doji(df, body_threshold=0.2, shadow_multiplier=1.5):
    """Детектирует доджи (смягченные пороги)."""
    df = df.copy().reset_index(drop=True)
    body = abs(df['close'] - df['open'])
    high_low = df['high'] - df['low']
    high_low = high_low.replace(0, np.nan)
    upper_shadow = df['high'] - df[['close', 'open']].max(axis=1)
    lower_shadow = df[['close', 'open']].min(axis=1) - df['low']
    is_doji = (body / high_low < body_threshold) & (upper_shadow > body * shadow_multiplier) & (lower_shadow > body * shadow_multiplier)
    return is_doji.fillna(False).astype(bool)

def find_consolidation_center(df, lookback=20):
    """Находит центр консолидации."""
    df = df.copy().reset_index(drop=True)
    rolling_high = df['high'].rolling(lookback).max()
    rolling_low = df['low'].rolling(lookback).min()
    center = (rolling_high + rolling_low) / 2
    return center

def detect_sweep(df, center, threshold=0.005):
    """Детектирует свип (увеличенный порог)."""
    df = df.copy().reset_index(drop=True)
    center = center.copy().reset_index(drop=True) if isinstance(center, pd.Series) else center
    
    # Если center - это число, делаем из него Series
    if not isinstance(center, pd.Series):
        center = pd.Series([center] * len(df), index=df.index)
    else:
        center = center.reset_index(drop=True)
    
    sweep_high = df['high'] > center * (1 + threshold)
    sweep_low = df['low'] < center * (1 - threshold)
    return_above = df['close'] > center
    return_below = df['close'] < center
    
    return {
        'bullish_sweep': sweep_low & return_above,
        'bearish_sweep': sweep_high & return_below,
        'sweep_high': sweep_high,
        'sweep_low': sweep_low,
    }

def calc_range_compression(df, window=20, avg_window=50):
    """Сужение диапазона."""
    df = df.copy().reset_index(drop=True)
    range_high = df['high'].rolling(window).max()
    range_low = df['low'].rolling(window).min()
    current_range = range_high - range_low
    avg_range = (df['high'] - df['low']).rolling(avg_window).mean()
    compression = current_range / avg_range.replace(0, np.nan)
    return compression

def calculate_adx(df, period=14):
    """ADX (Average Directional Index) — сила тренда."""
    df = df.copy().reset_index(drop=True)
    high = df['high']
    low = df['low']
    close = df['close']
    
    plus_dm = high.diff()
    minus_dm = -low.diff()
    
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    atr = tr.rolling(window=period).mean()
    plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr.replace(0, np.nan))
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(window=period).mean()
    
    return adx, plus_di, minus_di


def calculate_rsi(series, period=14):
    """RSI."""
    series = series.copy().reset_index(drop=True)
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_atr(df, period=14):
    """ATR."""
    df = df.copy().reset_index(drop=True)
    high_low = df['high'] - df['low']
    high_close = abs(df['high'] - df['close'].shift())
    low_close = abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def extract_micro_features(df_1h):
    """Признаки для 1H."""
    df = df_1h.copy().reset_index(drop=True)
    features = pd.DataFrame(index=df.index)
    features['doji'] = detect_doji(df).astype(float)
    features['rsi_14'] = calculate_rsi(df['close'], 14)
    features['atr_14'] = calculate_atr(df, 14)
    features['volume_ratio'] = df['volume'] / df['volume'].rolling(20).mean().replace(0, np.nan)
    features['close_sma_20'] = df['close'].rolling(20).mean()
    features['momentum_10'] = df['close'].pct_change(10)
    features['volatility'] = df['close'].pct_change().rolling(20).std()
    features['range'] = (df['high'] - df['low']) / df['close']
    return features.fillna(0)

def extract_meso_features(df_4h, df_1d=None):
    """Признаки для 4H."""
    df = df_4h.copy().reset_index(drop=True)
    features = pd.DataFrame(index=df.index)
    
    center = find_consolidation_center(df, lookback=10)
    features['center'] = center
    features['dist_to_center'] = (df['close'] - center) / center.replace(0, np.nan)
    
    sweep = detect_sweep(df, center)
    features['bullish_sweep'] = sweep['bullish_sweep'].astype(float)
    features['bearish_sweep'] = sweep['bearish_sweep'].astype(float)
    
    features['doji'] = detect_doji(df).astype(float)
    features['compression'] = calc_range_compression(df, window=10, avg_window=20)
    features['rsi'] = calculate_rsi(df['close'], 14)
    features['atr'] = calculate_atr(df, 14)
    features['volume_ratio'] = df['volume'] / df['volume'].rolling(20).mean().replace(0, np.nan)
    
    if df_1d is not None:
        df_d = df_1d.copy().reset_index(drop=True)
        center_d = find_consolidation_center(df_d, lookback=20)
        # Ресаймплим
        center_d_resampled = center_d.reindex(df.index, method='ffill').fillna(center_d.mean())
        features['center_1d'] = center_d_resampled
        features['dist_to_1d_center'] = (df['close'] - center_d_resampled) / center_d_resampled.replace(0, np.nan)
    
    return features.fillna(0)

def extract_macro_features(df_1d):
    """Признаки для 1D."""
    df = df_1d.copy().reset_index(drop=True)
    features = pd.DataFrame(index=df.index)
    center = find_consolidation_center(df, lookback=20)
    features['center'] = center
    features['dist_to_center'] = (df['close'] - center) / center.replace(0, np.nan)
    features['rsi'] = calculate_rsi(df['close'], 14)
    features['atr'] = calculate_atr(df, 14)
    features['volume_ratio'] = df['volume'] / df['volume'].rolling(20).mean().replace(0, np.nan)
    features['range'] = (df['high'] - df['low']) / df['close']
    return features.fillna(0)