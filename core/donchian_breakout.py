# -*- coding: utf-8 -*-
"""Donchian breakout + ATR trailing — боевая реализация.

Параметры зафиксированы в H24 (backtest_results/h24_unified_cost.txt):
    lookback 20, ATR(20) трейлинг x2.0, time-stop 30 баров,
    риск 0.35% на сделку, лимит 6 одновременных позиций,
    остановка при просадке 20%.

Модуль самодостаточен и НЕ зависит от ограничений фреймворка:
    - ATR-трейлинг (TrailingManager умеет только R-based breakeven/step);
    - time-stop по открытой позиции (в position_tracker его нет);
    - отсутствие фиксированного TP (tp_price в position_tracker обязателен).

Сигнал и выходы возвращаются решениями, ордера не выставляются. Это
делает логику проверяемой тестом паритета против бэктеста.

Инварианты, нарушение которых ломает паритет с бэктестом:
    - уровень Дончиана сдвинут на 1 бар, текущий бар в сравнение не входит
      (иначе пробой сравнивается с собственным максимумом);
    - ATR фиксируется на баре входа и далее не пересчитывается;
    - стоп проверяется ДО обновления экстремума внутри бара;
    - стоп проверяется ДО time-stop.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

LONG, SHORT = 1, -1

SIDE_NAME = {LONG: 'LONG', SHORT: 'SHORT'}
NAME_SIDE = {'BUY': LONG, 'LONG': LONG, 'SELL': SHORT, 'SHORT': SHORT}


@dataclass(frozen=True)
class DonchianConfig:
    """Параметры стратегии. Defaults = валидированные в H24."""

    lookback: int = 20
    atr_period: int = 20
    trail_atr: float = 2.0
    max_bars: int = 30
    risk_percent: float = 0.35
    max_concurrent: int = 6
    max_drawdown_halt: float = 20.0

    @property
    def risk_fraction(self) -> float:
        return self.risk_percent / 100.0


# ─── индикаторы ────────────────────────────────────────────────────

def true_range(high: np.ndarray, low: np.ndarray,
               close: np.ndarray) -> np.ndarray:
    """TR как в H24: max(H-L, |H-prevC|, |L-prevC|). На первом баре prevC=NaN,
    nanmax даёт H-L — это же поведение требуется для паритета."""
    prev_close = np.r_[np.nan, close[:-1]]
    stacked = np.vstack([high - low,
                         np.abs(high - prev_close),
                         np.abs(low - prev_close)])
    with np.errstate(invalid='ignore'):
        return np.nanmax(stacked, axis=0)


def atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               period: int) -> np.ndarray:
    """SMA от TR. Не Wilder — паритет с H24 требует именно SMA."""
    return pd.Series(true_range(high, low, close)).rolling(period).mean().to_numpy()


def donchian_levels(high: np.ndarray, low: np.ndarray,
                    lookback: int) -> tuple[np.ndarray, np.ndarray]:
    """Верхний/нижний уровень Дончиана по high/low, сдвинутые на 1 бар."""
    up = pd.Series(high).rolling(lookback).max().shift(1).to_numpy()
    dn = pd.Series(low).rolling(lookback).min().shift(1).to_numpy()
    return up, dn


def signal_series(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                  lookback: int) -> np.ndarray:
    """+1 long, -1 short, 0 нет сигнала."""
    up, dn = donchian_levels(high, low, lookback)
    sig = np.zeros(len(close), dtype=float)
    sig[close > up] = 1.0
    sig[close < dn] = -1.0
    sig[np.isnan(up)] = 0.0
    return sig


def position_size(entry: float, atr: float, cfg: DonchianConfig,
                  equity: float) -> float:
    """qty = equity * risk / (trail_atr * atr).

    risk_unit = trail_atr * atr, поэтому начальный стоп находится ровно
    в 1R от входа и R-кратности в бэктесте совпадают.
    """
    risk_unit = cfg.trail_atr * atr
    if not (risk_unit > 0) or not (equity > 0) or not math.isfinite(atr):
        return 0.0
    return (equity * cfg.risk_fraction) / risk_unit


# ─── сигнальный генератор ──────────────────────────────────────────

class DonchianSignalGenerator:
    """Контракт совместим с ZDevSignalGenerator.process_candle.

    entry — опорная цена (close бара сигнала) для расчёта стопа и risk_unit.
    Фактическая заливка берётся у биржи; risk_unit = trail_atr * ATR
    не зависит от цены заливки.
    """

    def __init__(self, symbol: str = '', cfg: DonchianConfig | None = None):
        self.symbol = symbol
        self.cfg = cfg or DonchianConfig()

    def evaluate(self, df: pd.DataFrame, i: int) -> Optional[dict]:
        """Оценить бар i. Требует минимум lookback+atr_period баров."""
        warmup = max(self.cfg.lookback, self.cfg.atr_period) + 1
        if i < warmup or i >= len(df):
            return None

        c = df['close'].to_numpy(float)
        h = df['high'].to_numpy(float)
        l = df['low'].to_numpy(float)

        atr = atr_series(h, l, c, self.cfg.atr_period)[i]
        if not np.isfinite(atr) or atr <= 0:
            return None

        up, dn = donchian_levels(h, l, self.cfg.lookback)
        if not (np.isfinite(up[i]) and np.isfinite(dn[i])):
            return None

        close = c[i]
        if close > up[i]:
            side = LONG
        elif close < dn[i]:
            side = SHORT
        else:
            return None

        ref = float(close)
        stop = ref - side * self.cfg.trail_atr * atr
        ts = df['timestamp'].iloc[i] if 'timestamp' in df.columns else df.index[i]

        return {
            'direction': 'BUY' if side == LONG else 'SELL',
            'side': side,
            'entry': ref,
            'stop': float(stop),
            'tp': None,
            'has_tp': False,
            'atr': float(atr),
            'donchian_level': float(up[i] if side == LONG else dn[i]),
            'atr_pct': float(atr / ref) if ref else None,
            'risk_unit': float(self.cfg.trail_atr * atr),
            'timestamp': str(ts),
            'strategy': 'donchian',
            'extras': {
                'lookback': self.cfg.lookback,
                'atr_period': self.cfg.atr_period,
                'trail_atr': self.cfg.trail_atr,
                'max_bars': self.cfg.max_bars,
            },
        }

    def process_candle(self, df: pd.DataFrame, candle_index: int) -> Optional[dict]:
        """Имя метода совпадает с ZDevSignalGenerator — подключается тем же кодом."""
        try:
            return self.evaluate(df, candle_index)
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                f"Donchian eval error {self.symbol}: {exc}")
            return None


# ─── выходы ────────────────────────────────────────────────────────

class DonchianExit:
    """Состояние открытой позиции: ATR-трейлинг + time-stop.

    bar_idx — индекс бара входа; bars_held = текущий индекс − bar_idx,
    поэтому time-stop срабатывает ровно через max_bars баров.
    """

    def __init__(self, side: int, entry: float, atr_entry: float,
                 bar_idx: int, cfg: DonchianConfig | None = None):
        if side not in (LONG, SHORT):
            raise ValueError(f"side must be {LONG} or {SHORT}, got {side!r}")
        if not (atr_entry > 0):
            raise ValueError(f"atr_entry must be > 0, got {atr_entry!r}")
        self.cfg = cfg or DonchianConfig()
        self.side = side
        self.entry = float(entry)
        self.atr_entry = float(atr_entry)
        self.bar_idx = int(bar_idx)
        self.stop = self.entry - side * self.cfg.trail_atr * self.atr_entry
        self.peak = self.entry
        self.exit_bar: Optional[int] = None
        self.exit_price: Optional[float] = None
        self.exit_reason: Optional[str] = None

    @property
    def risk_unit(self) -> float:
        return self.cfg.trail_atr * self.atr_entry

    @property
    def closed(self) -> bool:
        return self.exit_reason is not None

    def bars_held(self, bar_idx: int) -> int:
        return int(bar_idx) - self.bar_idx

    def _finalize(self, reason: str, price: float, bar_idx: int) -> dict:
        self.exit_bar = int(bar_idx)
        self.exit_price = float(price)
        self.exit_reason = reason
        return {
            'reason': reason,
            'price': float(price),
            'bar': int(bar_idx),
            'bars_held': self.bars_held(bar_idx),
            'r_multiple': ((price - self.entry) * self.side) / self.risk_unit,
        }

    def evaluate(self, bar_idx: int, high: float, low: float,
                 close: float) -> Optional[dict]:
        """Решение по бару. Стоп проверяется ДО time-stop.

        Возвращает None, если позиция остаётся открытой.
        """
        if self.closed:
            raise RuntimeError("evaluate() on closed DonchianExit")

        hit = low <= self.stop if self.side == LONG else high >= self.stop
        if hit:
            return self._finalize('STOP', self.stop, bar_idx)
        if self.bars_held(bar_idx) >= self.cfg.max_bars:
            return self._finalize('TIME', close, bar_idx)
        return None

    def update(self, high: float, low: float) -> None:
        """Подвинуть стоп по экстремуму бара. Вызывать только если не закрылись."""
        if self.closed:
            raise RuntimeError("update() on closed DonchianExit")
        dist = self.cfg.trail_atr * self.atr_entry
        if self.side == LONG:
            self.peak = max(self.peak, high)
            self.stop = max(self.stop, self.peak - dist)
        else:
            self.peak = min(self.peak, low)
            self.stop = min(self.stop, self.peak + dist)

    def on_bar(self, bar_idx: int, high: float, low: float,
               close: float) -> Optional[dict]:
        """evaluate + update в порядке бара. Удобно для теста и для live."""
        decision = self.evaluate(bar_idx, high, low, close)
        if decision is None:
            self.update(high, low)
        return decision


class DrawdownHalt:
    """Остановка торговли при просадке. Не уменьшает риск, а именно
    останавливает: масштабирование риска проверялось в H23 и не окупается
    (режет доходность в ~15 раз сильнее просадки)."""

    def __init__(self, limit_percent: float = 20.0):
        self.limit = limit_percent / 100.0
        self.peak = 0.0
        self.halted = False

    def update(self, equity: float) -> float:
        self.peak = max(self.peak, equity)
        dd = (self.peak - equity) / self.peak if self.peak > 0 else 0.0
        if dd >= self.limit:
            self.halted = True
        return dd

    def resume(self) -> None:
        self.halted = False
        self.peak = 0.0
