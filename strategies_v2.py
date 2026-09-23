"""
Strategies v2 — «Центр бочонка» + «Пересечение equilibrium».
Всё из изолированной логики (не трогаем существующего бота).
Асимметрия для бочонка поддерживает 3 варианта:
  A (candles)  — ratio по количеству свечей каскада до границ
  B (atr)      — ratio по дистанции, нормализованной ATR
  C (hybrid)   — Y-ratio 1.4-1.8 + ATR-ratio 1.3-2.0 + каскад >= 3 свечей
Параметры НЕ оптимизированы под историю — широкие диапазоны (Fix.txt/fix2).
"""
import numpy as np
import pandas as pd
from smc_features import calculate_atr

DEFAULT_CFG = {
    'lookback': 20,
    'z_threshold': 2.0,
    'body_ratio_max': 0.40,
    'ratio_y': [1.4, 1.8],
    'ratio_atr': [1.3, 2.0],
    'min_cascade': 3,
    'mid_zone_pct': 0.20,      # не входить, если цена в середине диапазона
    'max_crosses_10': 2,       # equ-cross: > 2 пересечений за 10 свечей = шум
    'tp1_frac': 0.5,           # equ-cross TP1 = eq + 0.5*(boundary - eq)
    'senior_lookback_days': 20,
    'zdev_dev_min': 0.15,      # zdev R: мин. отклонение от eq в долях ширины диапазона
    'zdev_atr_min': 3.0,       # zdev A: мин. отклонение от eq в ATR
}


def _senior_daily(df, lookback=20):
    """Daily resample + rolling 1D range для проверки старшего ТФ."""
    d = df.copy().reset_index(drop=True)
    d['_date'] = d['timestamp'].dt.date
    daily = d.groupby('_date').agg(
        high=('high', 'max'), low=('low', 'min'), close=('close', 'last')
    ).reset_index()
    daily['_rh'] = daily['high'].rolling(lookback).max()
    daily['_rl'] = daily['low'].rolling(lookback).min()
    daily['_eq'] = (daily['_rh'] + daily['_rl']) / 2
    m = d.merge(daily[['_date', '_rh', '_rl', '_eq']], on='_date', how='left')
    return m


class StrategiesV2:
    """Обе стратегии. precompute() один раз, потом по индексу."""

    def __init__(self, df, cfg=None):
        cfg = {**DEFAULT_CFG, **(cfg or {})}
        self.cfg = cfg
        self.n = cfg['lookback']
        self.df = df.reset_index(drop=True).copy()
        self._precompute()

    def _precompute(self):
        d = self.df
        n = self.n
        high = d['high']
        low = d['low']
        close = d['close']
        vol = d['volume']

        d['range_high'] = high.rolling(n).max()
        d['range_low'] = low.rolling(n).min()
        d['eq'] = (d['range_high'] + d['range_low']) / 2
        d['range_width'] = (d['range_high'] - d['range_low']).replace(0, np.nan)

        vmean = vol.rolling(n).mean()
        vstd = vol.rolling(n).std()
        d['z'] = (vol - vmean) / vstd.replace(0, np.nan)

        d['body_ratio'] = (close - d['open']).abs() / (high - low).replace(0, np.nan)
        d['atr'] = calculate_atr(d, period=14)

        # Индекс свечи, где стоял max/min за окно → длина каскада до границы
        win = np.arange(n)
        def _argmax_pos(a):
            if len(a) < n:
                return np.nan
            return int(np.argmax(a))
        def _argmin_pos(a):
            if len(a) < n:
                return np.nan
            return int(np.argmin(a))
        d['_argmax'] = high.rolling(n).apply(_argmax_pos, raw=True)
        d['_argmin'] = low.rolling(n).apply(_argmin_pos, raw=True)

        # senior 1D
        d['_date'] = d['timestamp'].dt.date
        daily = d.groupby('_date').agg(
            high=('high', 'max'), low=('low', 'min'), close=('close', 'last')
        ).reset_index()
        daily['_drh'] = daily['high'].rolling(self.cfg['senior_lookback_days']).max()
        daily['_drl'] = daily['low'].rolling(self.cfg['senior_lookback_days']).min()
        daily['_deq'] = (daily['_drh'] + daily['_drl']) / 2
        m = d.merge(daily[['_date', '_drh', '_drl', '_deq']], on='_date', how='left')
        for c in ['_drh', '_drl', '_deq']:
            d[c] = m[c].values

    # ─── helpers ──────────────────────────────────────────────
    def _pos(self, i):
        n, d = self.n, self.df
        argmax = d['_argmax'].iloc[i]
        argmin = d['_argmin'].iloc[i]
        if np.isnan(argmax) or np.isnan(argmin):
            return None
        cascade_high = abs(i - (i - (n - 1) + int(argmax))) + 1
        cascade_low = abs(i - (i - (n - 1) + int(argmin))) + 1
        return cascade_high, cascade_low

    def _near_mid(self, price, rh, rl):
        """True, если цена в мёртвой середине диапазона (внутри mid_zone_pct от равновесия)."""
        if pd.isna(rh) or pd.isna(rl) or rh <= rl:
            return False
        w = rh - rl
        return abs(price - (rh + rl) / 2) < w * self.cfg['mid_zone_pct']

    def _senior_mid(self, i):
        """Старший ТФ (1D): не входить, если цена в середине дневного диапазона."""
        d, cfg = self.df, self.cfg
        rh, rl = d['_drh'].iloc[i], d['_drl'].iloc[i]
        if pd.isna(rh) or pd.isna(rl) or rh <= rl:
            return False
        return self._near_mid(d['close'].iloc[i], rh, rl)

    def _z_ok(self, i):
        z = self.df['z'].iloc[i]
        return not pd.isna(z) and z >= self.cfg['z_threshold']

    # ─── Strategy 1: Центр бочонка ────────────────────────────
    def bochonok(self, i, variant='C'):
        d, cfg = self.df, self.cfg
        if i < self.n:
            return None
        body_ratio = d['body_ratio'].iloc[i]
        if pd.isna(body_ratio) or body_ratio > cfg['body_ratio_max']:
            return None
        if not self._z_ok(i):
            return None

        rh, rl = d['range_high'].iloc[i], d['range_low'].iloc[i]
        if pd.isna(rh) or pd.isna(rl):
            return None

        center = (d['high'].iloc[i] + d['low'].iloc[i]) / 2
        dist_h = rh - center
        dist_l = center - rl
        if dist_h <= 0 or dist_l <= 0:
            return None

        # Старший ТФ (1D) в середине — не входить
        if self._senior_mid(i):
            return None

        cascade_high, cascade_low = self._pos(i)
        atr = d['atr'].iloc[i]
        atr_h = dist_h / atr if atr and not pd.isna(atr) else np.nan
        atr_l = dist_l / atr if atr and not pd.isna(atr) else np.nan
        y_ratio = max(dist_h, dist_l) / min(dist_h, dist_l)
        atr_ratio = (max(atr_h, atr_l) / min(atr_h, atr_l)) if (atr_h and atr_l and min(atr_h, atr_l) > 0) else np.nan
        c_ratio = max(cascade_high, cascade_low) / min(cascade_high, cascade_low) if min(cascade_high, cascade_low) > 0 else np.nan

        r_y = cfg['ratio_y']
        ok_y = r_y[0] <= y_ratio <= r_y[1]
        ok_atr = (not pd.isna(atr_ratio)) and cfg['ratio_atr'][0] <= atr_ratio <= cfg['ratio_atr'][1]
        ok_casc = min(cascade_high, cascade_low) >= cfg['min_cascade']

        if variant == 'A':
            ok = ok_casc and (not pd.isna(c_ratio)) and r_y[0] <= c_ratio <= r_y[1]
        elif variant == 'B':
            ok = ok_atr
        else:  # C hybrid
            ok = ok_y and ok_atr and ok_casc

        if not ok:
            return None

        # Центр ближе к range_low → ЛОНГ к балансу (вверх), иначе SELL
        eq = d['eq'].iloc[i]
        direction = 'BUY' if dist_l < dist_h else 'SELL'
        if direction == 'BUY':
            stop = min(rl, d['low'].iloc[i])
            tp = eq                       # Цель 1: equilibrium
            if cfg.get('tp_target') == 'boundary':
                tp = rh                  # Цель 2: противоположная граница
            elif cfg.get('tp_target') == 'fib':
                tp = 1.61 * center       # Цель 3: 1.61 * bochonok_center
        else:
            stop = max(rh, d['high'].iloc[i])
            tp = eq
            if cfg.get('tp_target') == 'boundary':
                tp = rl
            elif cfg.get('tp_target') == 'fib':
                tp = 1.61 * center

        if stop == center or tp is None:
            return None

        return {
            'direction': direction,
            'entry': center,
            'stop': stop,
            'tp': tp,
            'timestamp': str(d['timestamp'].iloc[i]),
            'confidence': 0.7,
            'variant': variant,
            'y_ratio': round(y_ratio, 3),
            'atr_ratio': round(atr_ratio, 3) if not pd.isna(atr_ratio) else None,
            'c_ratio': round(c_ratio, 3) if not pd.isna(c_ratio) else None,
            'z': round(d['z'].iloc[i], 2),
            'body_ratio': round(body_ratio, 3),
        }

    # ─── Strategy 3: Z-Order + отклонение от equilibrium ──────
    def z_deviation(self, i, variant='R'):
        """Только Z-Order свеча (z >= z_threshold) и цена на отклонении от eq.
        R — отклонение в % от ширины диапазона (zdev_dev_min),
        A — отклонение в ATR (zdev_atr_min).
        Вход в сторону равновесия. Стоп за границу диапазона."""
        d, cfg = self.df, self.cfg
        if i < self.n:
            return None
        if not self._z_ok(i):
            return None

        eq = d['eq'].iloc[i]
        rh, rl = d['range_high'].iloc[i], d['range_low'].iloc[i]
        if pd.isna(eq) or pd.isna(rh) or pd.isna(rl) or rh <= rl:
            return None

        price = d['close'].iloc[i]
        w = rh - rl

        if variant == 'A':
            atr = d['atr'].iloc[i]
            if atr is None or pd.isna(atr) or atr <= 0:
                return None
            dev_min = cfg.get('zdev_atr_min', 3.0)
            below = price <= eq - atr * dev_min
            above = price >= eq + atr * dev_min
        else:
            dev_min = cfg.get('zdev_dev_min', 0.15)
            below = price <= eq - w * dev_min
            above = price >= eq + w * dev_min

        if not (below or above):
            return None
        if self._senior_mid(i):
            return None

        if below:
            direction = 'BUY'
            stop = min(rl, d['low'].iloc[i])
            tp = eq
        else:
            direction = 'SELL'
            stop = max(rh, d['high'].iloc[i])
            tp = eq

        if stop == price or tp is None:
            return None

        return {
            'direction': direction,
            'entry': price,
            'stop': stop,
            'tp': tp,
            'timestamp': str(d['timestamp'].iloc[i]),
            'confidence': 0.6,
            'z': round(d['z'].iloc[i], 2),
            'dev_frac': round((price - eq) / w, 3),
        }

    # ─── Strategy 2: Пересечение equilibrium ──────────────────
    def equilibrium_cross(self, i):
        d, cfg = self.df, self.cfg
        if i < self.n:
            return None
        if not self._z_ok(i):
            return None

        eq = d['eq'].iloc[i]
        if pd.isna(eq):
            return None
        prev_close = d['close'].iloc[i - 1]
        curr_close = d['close'].iloc[i]
        crossed_up = prev_close < eq and curr_close > eq
        crossed_down = prev_close > eq and curr_close < eq
        if not (crossed_up or crossed_down):
            return None

        # Шум: > 2 пересечений за последние 10 свечей
        window = d['close'].iloc[max(0, i - 10):i]
        eq_series = d['eq'].iloc[max(0, i - 10):i]
        if len(window) > 0 and len(eq_series) > 0:
            signs = (window.values > eq_series.values).astype(int)
            crosses = int(np.abs(np.diff(signs)).sum())
            if crosses > cfg['max_crosses_10']:
                return None

        rh, rl = d['range_high'].iloc[i], d['range_low'].iloc[i]
        if pd.isna(rh) or pd.isna(rl):
            return None
        if self._senior_mid(i):
            return None

        high_i, low_i = d['high'].iloc[i], d['low'].iloc[i]

        if crossed_up:
            direction = 'BUY'
            stop = min(low_i, rl)
            tp = eq + cfg['tp1_frac'] * (rh - eq)
        else:
            direction = 'SELL'
            stop = max(high_i, rh)
            tp = eq - cfg['tp1_frac'] * (eq - rl)

        if stop == curr_close or tp is None:
            return None

        return {
            'direction': direction,
            'entry': curr_close,
            'stop': stop,
            'tp': tp,
            'timestamp': str(d['timestamp'].iloc[i]),
            'confidence': 0.6,
            'z': round(d['z'].iloc[i], 2),
            'crossed_up': crossed_up,
        }


def generate_signals(df, strategy_name, variant=None, cfg=None):
    """Вернёт список сигналов в формате, совместимом с run_backtest."""
    d = df.reset_index(drop=True).copy()
    s = StrategiesV2(d, cfg=cfg)
    signals = []
    for i in range(len(d)):
        if strategy_name == 'bochonok':
            sig = s.bochonok(i, variant=variant)
        elif strategy_name == 'zdev':
            sig = s.z_deviation(i, variant=variant)
        else:
            sig = s.equilibrium_cross(i)
        if sig:
            signals.append(sig)
    return signals