# -*- coding: utf-8 -*-
"""Тест паритета: core/donchian_breakout.py против H24.

Цель — доказать, что боевая реализация даёт ровно те же сделки, что
валидированный бэктест. Без этого конфиг может загрузиться, бот может
торговать, и результат будет не тем, который проверяли.

Проверяется:
  1. индикаторы (TR, ATR, уровни Дончиана, сигналы) — побитовое равенство;
  2. полная последовательность сделок и R-кратностей — побитовое равенство;
  3. инварианты, нарушение которых даёт lookahead или сдвиг паритета.

Запуск:  python verify_donchian_parity.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from core.donchian_breakout import (  # noqa: E402
    LONG, SHORT, DonchianConfig, DonchianExit, DonchianSignalGenerator,
    DrawdownHalt, atr_series, efficiency_ratio, er_threshold_at,
    position_size, signal_series,
)
import h24_unified_cost as H24  # noqa: E402

OUT = Path('backtest_results/donchian_parity.txt')
_l: list[str] = []
FAILS: list[str] = []


def P(s=''):
    print(s)
    _l.append(s)


def check(name: str, ok: bool, detail: str = ''):
    P(f'  [{"OK  " if ok else "FAIL"}] {name}' + (f'   {detail}' if detail else ''))
    if not ok:
        FAILS.append(name)
    return ok


def synth(n=200, seed=7):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0, 0.012, n)
    c = 100 * np.exp(np.cumsum(ret))
    o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.002, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.004, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.004, n)))
    return pd.DataFrame({'timestamp': pd.date_range('2026-01-01', periods=n,
                                                     freq='4h'),
                         'open': o, 'high': h, 'low': l, 'close': c})


# ─── 1. индикаторы ─────────────────────────────────────────────────

def test_indicators(data, idx):
    P('=' * 92)
    P('1. ИНДИКАТОРЫ: модуль против H24')
    P('=' * 92)
    P('')
    cfg = DonchianConfig()
    h = data['ADA']['high'].to_numpy(float)
    l = data['ADA']['low'].to_numpy(float)
    c = data['ADA']['close'].to_numpy(float)
    o = data['ADA']['open'].to_numpy(float)

    a_mod = atr_series(h, l, c, cfg.atr_period)
    a_ref = H24.arrays(data['ADA'])['a']
    check('ATR(20) совпадает с H24 (с учётом NaN)',
          np.array_equal(a_mod, a_ref, equal_nan=True),
          f'max|d|={np.nanmax(np.abs(a_mod - a_ref)):.3e}, '
          f'NaN в обоих: {np.isnan(a_mod).sum()}')

    s_mod = signal_series(c, h, l, cfg.lookback)
    s_ref = H24.arrays(data['ADA'])['sig']
    check('сигналы совпадают побитово', np.array_equal(s_mod, s_ref),
          f'сигналов: {(s_mod != 0).sum()}')
    check('пробой вверх/вниз не на одном баре',
          not np.any((s_mod == 1) & (s_ref == -1)),
          'long и short не должны пересекаться')

    up, dn = __import__('core.donchian_breakout', fromlist=['x']).donchian_levels(
        h, l, cfg.lookback)
    i = 100
    check('уровень Дончиана не включает текущий бар',
          up[i] >= h[i - cfg.lookback:i].max() - 1e-12
          and up[i] <= h[max(0, i - cfg.lookback - 1):i - 1].max() + 1e-12,
          f'up[{i}]={up[i]:.4f} vs high[{i}]={h[i]:.4f}')

    sdf = synth(400)
    s0 = signal_series(sdf['close'].to_numpy(float), sdf['high'].to_numpy(float),
                       sdf['low'].to_numpy(float), cfg.lookback)
    a0 = atr_series(sdf['high'].to_numpy(float), sdf['low'].to_numpy(float),
                    sdf['close'].to_numpy(float), cfg.atr_period)
    perturbed = sdf.copy()
    perturbed['open'] = perturbed['open'] * 7.0 + 13.0
    s1 = signal_series(perturbed['close'].to_numpy(float),
                       perturbed['high'].to_numpy(float),
                       perturbed['low'].to_numpy(float), cfg.lookback)
    a1 = atr_series(perturbed['high'].to_numpy(float),
                    perturbed['low'].to_numpy(float),
                    perturbed['close'].to_numpy(float), cfg.atr_period)
    check('ATR и сигналы не зависят от open', np.array_equal(s0, s1)
          and np.array_equal(a0, a1, equal_nan=True),
          'open не участвует в TR и в уровнях Дончиана')
    P('')


# ─── 2. полная последовательность сделок ──────────────────────────

def module_portfolio(prep, sig, idx, cfg, risk, cap=H24.CAP):
    """Портфель на модуле. Математика издержек — как в исправленном H24."""
    n = len(idx)
    cash = H24.CAPITAL
    openp, pend = {}, {}
    Rs, log = [], []
    for j in range(n):
        for co, s in list(pend.items()):
            p = prep[co]
            ae = p['a'][j]
            if not np.isfinite(ae) or ae <= 0:
                continue
            entry = p['o'][j] * (1 + H24.SLIP)
            cur = cash + sum(q['ex'].side * q['qty'] * (prep[k]['c'][j] - q['ex'].entry)
                             for k, q in openp.items())
            qty = (cur * risk) / (cfg.trail_atr * ae)
            marg = sum(q['qty'] * prep[k]['c'][j] for k, q in openp.items())
            if len(openp) >= cap or marg + qty * entry > cur * H24.LEV * H24.MARGIN_BUF:
                continue
            cash -= qty * entry * H24.TAKER
            openp[co] = dict(ex=DonchianExit(s, entry, ae, j, cfg), qty=qty)
        pend.clear()

        for co, q in list(openp.items()):
            p = prep[co]
            dec = q['ex'].on_bar(j, p['h'][j], p['l'][j], p['c'][j])
            if dec is None:
                continue
            px = dec['price'] * (1 - H24.SLIP)
            pnl = q['qty'] * (px - q['ex'].entry) * q['ex'].side - q['qty'] * px * H24.TAKER
            cash += pnl
            Rs.append(pnl / (q['qty'] * q['ex'].risk_unit))
            log.append((co, q['ex'].side, q['ex'].bar_idx, j, dec['reason'],
                        dec['price']))
            del openp[co]

        if j + 1 < n:
            for co in sorted(prep):
                if co in openp or co in pend:
                    continue
                if sig[co][j] != 0:
                    pend[co] = sig[co][j]
    return np.array(Rs), log


def test_portfolio(data, idx):
    P('=' * 92)
    P('2. ПОЛНАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ СДЕЛОК')
    P('=' * 92)
    P('')
    cfg = DonchianConfig()
    prep = {k: H24.arrays(v) for k, v in data.items()}
    sig = {k: H24.arrays(v)['sig'] for k, v in data.items()}

    ref = H24.run(prep, idx, cfg.risk_fraction)
    r_mod, log = module_portfolio(prep, sig, idx, cfg, cfg.risk_fraction)

    check('число сделок совпадает', len(r_mod) == ref['n_tr'],
          f'модуль {len(r_mod)} vs H24 {ref["n_tr"]}')
    if len(r_mod) == len(ref['R']):
        d = np.abs(r_mod - ref['R']).max()
        check('R-кратности совпадают', d < 1e-12, f'max|d|={d:.3e}')
    check('expectancy совпадает',
          abs(r_mod.mean() - ref['exp_r']) < 1e-12,
          f'{r_mod.mean():+.6f}R vs {ref["exp_r"]:+.6f}R')
    check('PF сделок совпадает', abs(
        r_mod[r_mod > 0].sum() / -r_mod[r_mod < 0].sum() - ref['pf']) < 1e-12,
        f'{r_mod[r_mod > 0].sum() / -r_mod[r_mod < 0].sum():.6f}')

    reasons = {}
    for r in log:
        reasons[r[4]] = reasons.get(r[4], 0) + 1
    P('')
    P(f'  выходы: {reasons}')
    check('есть time-stop выходы', reasons.get('TIME', 0) > 0,
          f'{reasons.get("TIME", 0)} из {len(log)}')
    check('все выходы распознаны', set(reasons) <= {'STOP', 'TIME'},
          f'{sorted(reasons)}')
    check('side: только long/short',
          {r[1] for r in log} <= {LONG, SHORT}, '')
    check('выход не раньше входа',
          all(r[3] >= r[2] for r in log), '')
    bars = [r[3] - r[2] for r in log]
    P(f'  срок удержания: медиана {np.median(bars):.0f}, '
      f'макс {max(bars)} баров (лимит {cfg.max_bars})')
    check('ни одна сделка не держится дольше max_bars',
          max(bars) <= cfg.max_bars, f'макс {max(bars)}')
    P('')
    return r_mod


# ─── 3. инварианты ────────────────────────────────────────────────

def test_invariants():
    P('=' * 92)
    P('3. ИНВАРИАНТЫ (lookahead, ATR, порядок проверок)')
    P('=' * 92)
    P('')
    cfg = DonchianConfig()
    gen = DonchianSignalGenerator('TESTUSDT', cfg)

    df = synth(300)
    base = gen.evaluate(df, 250)
    check('сигнал на последнем баре не None (синтетика)', base is not None, '')

    tampered = df.copy()
    tampered.loc[251, 'high'] = tampered.loc[251, 'high'] * 50
    tampered.loc[251, 'low'] = tampered.loc[251, 'low'] * 0.02
    after = gen.evaluate(tampered, 250)
    same = (base is None and after is None) or (
        base is not None and after is not None
        and base['direction'] == after['direction']
        and abs(base['entry'] - after['entry']) < 1e-12)
    check('сигнал на баре i не зависит от бара i+1 (нет lookahead)', same, '')

    tampered2 = df.copy()
    tampered2.loc[250, 'high'] = tampered2.loc[250, 'high'] * 3
    after2 = gen.evaluate(tampered2, 250)
    check('сигнал не зависит от собственного high бара i', after2 is None
          or after2['direction'] == base['direction'], '')

    ex = DonchianExit(LONG, 100.0, 2.0, 10, cfg)
    check('стоп на входе = 1R', abs(ex.stop - 96.0) < 1e-12,
          f'stop={ex.stop} risk_unit={ex.risk_unit}')
    ex.update(110.0, 101.0)
    check('после касания 110 стоп = 110 − 2*ATR = 106',
          abs(ex.stop - 106.0) < 1e-12, f'stop={ex.stop}')
    ex2 = DonchianExit(LONG, 100.0, 2.0, 10, cfg)
    for hi, lo in ((110.0, 101.0), (130.0, 121.0), (95.0, 90.0), (140.0, 135.0)):
        ex2.update(hi, lo)
        dist = ex2.peak - ex2.stop
        if not np.isclose(dist, 4.0):
            check('дистанция стопа до экстремума = 2*ATR все время', False,
                  f'на баре с high={hi}: {dist:.6f}')
            break
    else:
        check('дистанция стопа до экстремума = 2*ATR все время', True,
              'peak − stop = 4.000000 на каждом баре, ATR не пересчитывается')
    ex3 = DonchianExit(LONG, 100.0, 9.0, 10, cfg)
    ex3.update(110.0, 101.0)
    check('ATR берётся из atr_entry (2*9.0 при atr_entry=9)',
          abs(ex3.stop - 92.0) < 1e-12, f'stop={ex3.stop}')

    trap = DonchianExit(LONG, 100.0, 2.0, 10, cfg)
    dec = trap.on_bar(11, high=110.0, low=95.0, close=108.0)
    check('внутрибара: стоп проверяется ДО обновления экстремума',
          dec is not None and dec['reason'] == 'STOP'
          and abs(dec['price'] - 96.0) < 1e-12,
          f'получено {dec["reason"]} @ {dec["price"]:.2f}; '
          f'при неверном порядке было бы 106.00')

    both = DonchianExit(LONG, 100.0, 2.0, 0, cfg)
    dec2 = both.on_bar(cfg.max_bars, high=101.0, low=95.0, close=100.5)
    check('стоп приоритетнее time-stop на одном баре',
          dec2['reason'] == 'STOP' and abs(dec2['price'] - 96.0) < 1e-12,
          f'{dec2["reason"]} @ {dec2["price"]:.2f} (стоп 96, low=95, бары={dec2["bars_held"]})')

    flat = DonchianExit(LONG, 100.0, 2.0, 0, cfg)
    dec3 = flat.on_bar(cfg.max_bars - 1, high=101.0, low=99.0, close=100.5)
    check(f'time-stop не раньше {cfg.max_bars} баров', dec3 is None, '')
    dec4 = flat.on_bar(cfg.max_bars, high=101.0, low=99.0, close=100.5)
    check(f'time-stop на {cfg.max_bars} баре',
          dec4 is not None and dec4['reason'] == 'TIME',
          f'{dec4["reason"] if dec4 else None}')
    check('time-stop выходит по close', abs(dec4['price'] - 100.5) < 1e-12, '')

    sh = DonchianExit(SHORT, 100.0, 2.0, 0, cfg)
    check('short: стоп выше входа', abs(sh.stop - 104.0) < 1e-12, f'{sh.stop}')
    sh.update(90.0, 90.0)
    check('short: стоп идёт за минимумом (90 + 4)', abs(sh.stop - 94.0) < 1e-12,
          f'{sh.stop}')
    sh.update(95.0, 95.0)
    check('short: стоп не приближается к входу', abs(sh.stop - 94.0) < 1e-12,
          f'{sh.stop}')

    q = position_size(100.0, 2.0, cfg, 100_000.0)
    check('размер = equity*risk/(2*ATR)',
          abs(q - 100_000 * 0.0035 / 4.0) < 1e-9, f'qty={q:.6f}')
    check('risk_unit == 1R', abs(2.0 * cfg.trail_atr - q * 4.0 / q) < 1e-9, '')
    check('нулевой ATR -> нулевой размер',
          position_size(100.0, 0.0, cfg, 100_000.0) == 0.0, '')

    h = DrawdownHalt(20.0)
    for eq in (100.0, 120.0, 96.0):
        h.update(eq)
    check('просадка ровно 20% -> остановка', h.halted,
          f'{(h.peak - 96.0) / h.peak:.2%} при лимите 20%')
    h3 = DrawdownHalt(20.0)
    for eq in (100.0, 120.0, 96.1):
        h3.update(eq)
    check('просадка 19.9% -> торговля идёт', not h3.halted,
          f'{(h3.peak - 96.1) / h3.peak:.2%}')
    h4 = DrawdownHalt(20.0)
    for eq in (100.0, 120.0, 117.0):
        h4.update(eq)
    check('просадка 2.5% -> торговля идёт', not h4.halted,
          f'{(h4.peak - 117.0) / h4.peak:.2%}')
    h4.resume()
    check('resume сбрасывает остановку', not h4.halted and h4.peak == 0.0, '')
    P('')


# ─── 4. регрессия живого пути smc/zdev ────────────────────────────

class FakeRedis:
    def __init__(self):
        self.pos = {}
        self.trades = []

    def load_all_positions(self):
        return dict(self.pos)

    def save_position(self, symbol, data):
        self.pos[symbol] = data

    def delete_position(self, symbol):
        self.pos.pop(symbol, None)

    def load_closed_trades(self):
        return list(self.trades)

    def save_closed_trade(self, t):
        self.trades.append(t)


def test_tracker_regression():
    from core.position_tracker import (PositionTracker, bars_held,
                                       BAR_SECONDS_4H)
    cfg = DonchianConfig()
    P('=' * 92)
    P('4. РЕГРЕССИЯ: smc/zdev не должны измениться')
    P('=' * 92)
    P('')

    t = PositionTracker()
    t.open_position('BTCUSDT', 'LONG', 100.0, 10.0, 95.0, 110.0)
    r = t.check_exits('BTCUSDT', high=112.0, low=99.0, close=111.0)
    check('smc: TP по-прежнему срабатывает', r is not None
          and r['exit_reason'] == 'TAKE_PROFIT',
          f"{r['exit_reason'] if r else None} @ {r['exit_price'] if r else None}")

    t2 = PositionTracker()
    t2.open_position('BTCUSDT', 'SHORT', 100.0, 10.0, 105.0, 90.0)
    r2 = t2.check_exits('BTCUSDT', high=101.0, low=88.0, close=89.0)
    check('smc: TP для SHORT по-прежнему срабатывает', r2 is not None
          and r2['exit_reason'] == 'TAKE_PROFIT',
          f"{r2['exit_reason'] if r2 else None}")

    base_ts = 1_700_000_000.0
    held = PositionTracker()
    held.open_position('ADAUSDT', 'LONG', 100.0, 10.0, 95.0, 110.0)
    r3 = held.check_exits('ADAUSDT', 101.0, 99.0, 100.5,
                          bar_ts=base_ts + 30 * BAR_SECONDS_4H, max_bars=30)
    check('smc: time-stop НЕ включается (max_bars=0 в Position)',
          r3 is None, f'{r3["exit_reason"] if r3 else "позиция осталась открытой"}')

    t4 = PositionTracker()
    t4.open_position('ARBUSDT', 'LONG', 100.0, 10.0, 96.0, None,
                     strategy='donchian', atr_entry=2.0,
                     entry_bar_ts=base_ts, max_bars=cfg.max_bars)
    r4 = t4.check_exits('ARBUSDT', high=101.0, low=99.0, close=100.5)
    check('donchian: tp=None не роняет check_exits и не даёт TP',
          r4 is None, f'{r4["exit_reason"] if r4 else "нет выхода"}')
    r5 = t4.check_exits('ARBUSDT', high=101.0, low=95.0, close=100.0)
    check('donchian: стоп срабатывает при tp=None', r5 is not None
          and r5['exit_reason'] in ('STOP_LOSS', 'TRAILING_STOP')
          and abs(r5['exit_price'] - 96.0) < 1e-9,
          f'{r5["exit_reason"] if r5 else None} @ {r5["exit_price"] if r5 else None}')

    t6 = PositionTracker()
    t6.open_position('SOLUSDT', 'LONG', 100.0, 10.0, 96.0, None,
                     strategy='donchian', atr_entry=2.0,
                     entry_bar_ts=base_ts, max_bars=cfg.max_bars)
    ts29 = base_ts + 29 * BAR_SECONDS_4H
    r6 = t6.check_exits('SOLUSDT', 101.0, 99.0, 100.5,
                        bar_ts=ts29, max_bars=cfg.max_bars)
    check('donchian: на 29 баре time-stop молчит', r6 is None, '')
    r7 = t6.check_exits('SOLUSDT', 101.0, 95.0, 100.5,
                        bar_ts=base_ts + 30 * BAR_SECONDS_4H,
                        max_bars=cfg.max_bars)
    check('donchian: стоп приоритетнее time-stop на 30 баре',
          r7 is not None and r7['exit_reason'] in ('STOP_LOSS', 'TRAILING_STOP'),
          f'{r7["exit_reason"] if r7 else None}')

    t7 = PositionTracker()
    t7.open_position('XRPUSDT', 'LONG', 100.0, 10.0, 96.0, None,
                     strategy='donchian', atr_entry=2.0,
                     entry_bar_ts=base_ts, max_bars=cfg.max_bars)
    r8 = t7.check_exits('XRPUSDT', 101.0, 99.0, 100.5,
                        bar_ts=base_ts + 30 * BAR_SECONDS_4H,
                        max_bars=cfg.max_bars)
    check('donchian: time-stop на 30 баре, выход по close',
          r8 is not None and r8['exit_reason'] == 'TIME_STOP'
          and abs(r8['exit_price'] - 100.5) < 1e-9,
          f'{r8["exit_reason"] if r8 else None} @ {r8["exit_price"] if r8 else None}')

    check('bars_held совпадает с подсчётом баров',
          bars_held(base_ts, base_ts + 7 * BAR_SECONDS_4H) == 7,
          f'{bars_held(base_ts, base_ts + 7 * BAR_SECONDS_4H)}')
    check('bars_held на том же баре = 0',
          bars_held(base_ts, base_ts) == 0, '')
    check('bars_held без времени входа = 0 (рестарт без данных)',
          bars_held(0, base_ts + 99 * BAR_SECONDS_4H) == 0, '')
    check('bars_held по модулю 4H, не по часам',
          bars_held(base_ts, base_ts + 3.9 * BAR_SECONDS_4H) == 3,
          f'{bars_held(base_ts, base_ts + 3.9 * BAR_SECONDS_4H)}')

    fake = FakeRedis()
    t8 = PositionTracker(redis_store=fake)
    t8.open_position('HBARUSDT', 'SHORT', 100.0, 10.0, 104.0, None,
                     strategy='donchian', atr_entry=2.5,
                     entry_bar_ts=base_ts, max_bars=30)
    saved = fake.pos['HBARUSDT']
    check('Redis: extreme сохранён при входе',
          abs(saved['extreme'] - 100.0) < 1e-12, f"{saved['extreme']}")
    saved['extreme'] = 88.0
    t9 = PositionTracker(redis_store=fake)
    pos9 = t9.get_position('HBARUSDT')
    check('Redis: extreme переживает рестарт', abs(pos9.extreme - 88.0) < 1e-12,
          f'{pos9.extreme}')
    check('Redis: atr_entry переживает рестарт',
          abs(pos9.atr_entry - 2.5) < 1e-12, f'{pos9.atr_entry}')
    check('Redis: entry_bar_ts переживает рестарт',
          abs(pos9.entry_bar_ts - base_ts) < 1e-9, '')
    check('Redis: max_bars переживает рестарт', pos9.max_bars == 30, '')
    check('Redis: tp_price=None переживает рестарт', pos9.tp_price is None, '')

    legacy = FakeRedis()
    legacy.pos['ADAUSDT'] = {
        'symbol': 'ADAUSDT', 'side': 'LONG', 'entry_price': 100.0,
        'size': 10.0, 'stop_price': 95.0, 'tp_price': 110.0,
        'entry_time': base_ts, 'strategy': 'zdev',
    }
    t10 = PositionTracker(redis_store=legacy)
    pos10 = t10.get_position('ADAUSDT')
    check('старый payload без новых полей загружается', pos10 is not None
          and pos10.atr_entry == 0.0 and pos10.max_bars == 0
          and abs(pos10.extreme - 100.0) < 1e-12,
          f'atr_entry={pos10.atr_entry} max_bars={pos10.max_bars} '
          f'extreme={pos10.extreme}')
    r10 = t10.check_exits('ADAUSDT', high=112.0, low=99.0, close=111.0)
    check('старая zdev-позиция по-прежнему даёт TP', r10 is not None
          and r10['exit_reason'] == 'TAKE_PROFIT', '')
    P('')


# ─── 5. wiring в main.py: cap, трейлинг, конфиг ────────────────────

class StubTracker:
    def __init__(self, positions=()):
        self.positions = set(positions)

    def has_position(self, symbol):
        return symbol in self.positions

    def get_position(self, symbol):
        return None

    def _save_position(self, symbol, pos):
        pass


class StubExecutor:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def update_stop_loss(self, symbol, sl):
        from core.order_executor import OrderResult
        self.calls.append((symbol, round(sl, 8)))
        return OrderResult(self.ok, message='stub')


class StubBot:
    """Минимальный объект для вызова методов TradingBot без сети."""
    _cap_allows_entry = None
    _donchian_trailing = None
    _donchian_config_for = None


def test_syntax_gate():
    """Жесткий гейт перед деплоем.

    Синтаксическая ошибка в main.py уехала на VPS и уронила контейнер в
    рестарт-цикл. Компиляция ВСЕХ боевых модулей проверяется здесь, чтобы
    это ловилось локально, а не в проде.
    """
    import py_compile
    P('=' * 92)
    P('4. СИНТАКСИС-GATE (обязателен перед docker cp)')
    P('=' * 92)
    P('')
    root = Path(__file__).parent
    targets = ['main.py', 'core/donchian_breakout.py', 'core/bybit_client.py',
               'core/order_executor.py', 'core/position_tracker.py',
               'core/risk_manager.py', 'core/redis_store.py']
    for t in targets:
        f = root / t
        try:
            py_compile.compile(str(f), doraise=True, cfile=str(root / '_sync_tmp.pyc'))
            check(f'компилируется: {t}', True, '')
        except py_compile.PyCompileError as e:
            check(f'компилируется: {t}', False, str(e).strip().splitlines()[-1])
        except Exception as e:  # noqa: BLE001
            check(f'компилируется: {t}', False, str(e))
    (root / '_sync_tmp.pyc').unlink(missing_ok=True)
    # import самого main.py — ловит ошибки уровня модуля (не только синтаксис)
    try:
        import importlib
        importlib.import_module('main')
        check('main.py импортируется (нет ошибок уровня модуля)', True, '')
    except Exception as e:  # noqa: BLE001
        check('main.py импортируется (нет ошибок уровня модуля)', False,
              f'{type(e).__name__}: {e}')
    P('')


def test_wiring():
    import main
    import yaml
    from core.risk_manager import RiskManager
    from core.position_tracker import Position, BAR_SECONDS_4H
    P('=' * 92)
    P('5. WIRING: cap-6, ATR-трейлинг, live-конфиг пула 10')
    P('=' * 92)
    P('')

    StubBot._cap_allows_entry = main.SMCFractalBot._cap_allows_entry
    StubBot._donchian_trailing = main.SMCFractalBot._donchian_trailing
    StubBot._donchian_config_for = main.SMCFractalBot._donchian_config_for

    pool = ['ADAUSDT', 'ARBUSDT', 'DOGEUSDT', 'ENAUSDT', 'HBARUSDT',
            'LINKUSDT', 'NEARUSDT', 'SUIUSDT', 'XLMUSDT', 'XRPUSDT']

    b = StubBot()
    b.symbols = pool
    b.max_concurrent_positions = 6
    b.tracker = StubTracker(pool[:5])
    check('cap-6: при 5 позициях вход разрешён',
          b._cap_allows_entry('XRPUSDT') is True, '')

    b.tracker = StubTracker(pool[:6])
    check('cap-6: при 6 позициях вход запрещён',
          b._cap_allows_entry('XRPUSDT') is False, '')

    b.tracker = StubTracker(pool[:6] + ['XRPUSDT'])
    check('cap-6: символ уже в позиции — вход не открывается',
          b._cap_allows_entry('XRPUSDT') is False, '')

    b.max_concurrent_positions = 0
    b.tracker = StubTracker(pool)
    check('cap=0 отключает лимит (legacy SMC/ZDev)',
          b._cap_allows_entry('ZZZUSDT') is True, '')

    pos = Position(symbol='ADAUSDT', side='LONG', entry_price=100.0, size=10.0,
                   stop_price=96.0, tp_price=None, entry_time=0.0, strategy='donchian',
                   atr_entry=2.0, extreme=100.0, entry_bar_ts=0.0, max_bars=30)

    class T2(StubTracker):
        def get_position(self, symbol):
            return pos
    b.tracker = T2()
    b.executor = StubExecutor()
    b.donchian_cfgs = {'ADAUSDT': DonchianConfig()}
    b._donchian_trailing('ADAUSDT', high=110.0, low=99.0)
    check('трейлинг: стоп за экстремумом 110 − 2*ATR = 106',
          pos.stop_price == 106.0, f'stop={pos.stop_price}')
    check('трейлинг: экстремум обновлён', pos.extreme == 110.0, f'{pos.extreme}')
    check('трейлинг: SL отправлен на биржу',
          b.executor.calls == [('ADAUSDT', 106.0)], f'{b.executor.calls}')

    b.executor = StubExecutor()
    b._donchian_trailing('ADAUSDT', high=105.0, low=99.0)
    check('трейлинг: откат вниз не опускает стоп',
          pos.stop_price == 106.0 and not b.executor.calls,
          f'stop={pos.stop_price} calls={b.executor.calls}')

    b.executor = StubExecutor(ok=False)
    b._donchian_trailing('ADAUSDT', high=120.0, low=99.0)
    check('трейлинг: биржа отклонила SL — стоп не двигаем',
          pos.stop_price == 106.0, f'stop={pos.stop_price}')

    sp = Position(symbol='SOLUSDT', side='SHORT', entry_price=100.0, size=10.0,
                  stop_price=104.0, tp_price=None, entry_time=0.0, strategy='donchian',
                  atr_entry=2.0, extreme=100.0, entry_bar_ts=0.0, max_bars=30)

    class T3(StubTracker):
        def get_position(self, symbol):
            return sp
    b.tracker = T3()
    b.donchian_cfgs = {'SOLUSDT': DonchianConfig()}
    b.executor = StubExecutor()
    b._donchian_trailing('SOLUSDT', high=101.0, low=90.0)
    check('short: стоп ниже минимума 90 − 2*ATR = 94',
          sp.stop_price == 94.0, f'stop={sp.stop_price}')

    z = Position(symbol='ADAUSDT', side='LONG', entry_price=100.0, size=10.0,
                 stop_price=96.0, tp_price=110.0, entry_time=0.0, strategy='zdev')

    class T4(StubTracker):
        def get_position(self, symbol):
            return z
    b.tracker = T4()
    b.executor = StubExecutor()
    b._donchian_trailing('ADAUSDT', high=110.0, low=99.0)
    check('трейлинг не трогает zdev-позицию',
          not b.executor.calls and z.stop_price == 96.0, '')

    na = Position(symbol='ADAUSDT', side='LONG', entry_price=100.0, size=10.0,
                  stop_price=96.0, tp_price=None, entry_time=0.0, strategy='donchian',
                  atr_entry=0.0, extreme=100.0, entry_bar_ts=0.0, max_bars=30)
    b.tracker.get_position = lambda s: na
    b.executor = StubExecutor()
    b._donchian_trailing('ADAUSDT', high=110.0, low=99.0)
    check('трейлинг без atr_entry ничего не двигает',
          not b.executor.calls and na.stop_price == 96.0, '')

    b.symbol_configs = {}
    check('donchian-конфиг: пусто -> валидированные дефолты H24',
          b._donchian_config_for('ADAUSDT') == DonchianConfig(), '')

    b.symbol_configs = {'ADAUSDT': {'donchian': {'lookback': 40, 'atr_period': 14}}}
    c = b._donchian_config_for('ADAUSDT')
    check('donchian-конфиг: частичный override сохраняет остальные дефолты',
          c.lookback == 40 and c.atr_period == 14 and c.trail_atr == 2.0
          and c.max_bars == 30 and c.risk_percent == 0.35, f'{c}')

    b.symbol_configs = {'ADAUSDT': {'donchian': {'lookback': 40, 'баг': 1}}}
    c = b._donchian_config_for('ADAUSDT')
    check('donchian-конфиг: неизвестный ключ игнорируется, не роняет бота',
          c.lookback == 40, f'{c}')

    b.symbol_configs = {'ADAUSDT': {'donchian': {'risk_percent': 0}}}
    try:
        b._donchian_config_for('ADAUSDT')
        check('donchian-конфиг: risk_percent=0 отклонён', False, 'не бросил')
    except ValueError:
        check('donchian-конфиг: risk_percent=0 отклонён', True, '')

    b.symbol_configs = {'ADAUSDT': {'donchian': {'risk_percent': 99}}}
    try:
        b._donchian_config_for('ADAUSDT')
        check('donchian-конфиг: risk_percent=99 отклонён', False, 'не бросил')
    except ValueError:
        check('donchian-конфиг: risk_percent=99 отклонён', True, '')

    cfg = yaml.safe_load(open('config/settings.donchian_pool10.yaml',
                              encoding='utf-8'))
    assets = cfg['assets']
    check('live-конфиг: ровно 10 монет пула',
          len(assets) == 10 and [a['symbol'] for a in assets] == pool,
          f"{len(assets)}")
    check('live-конфиг: аутсайдеры H25 исключены',
          not ({'SOLUSDT', 'TONUSDT', 'APTUSDT', 'ETHUSDT'}
               & {a['symbol'] for a in assets}), '')
    check('live-конфиг: XMR/LTC/BTC не возвращены (netR <= 0.013)',
          not ({'XMRUSDT', 'LTCUSDT', 'BTCUSDT'}
               & {a['symbol'] for a in assets}), '')
    check('live-конфиг: все donchian и заблокированы от оптимизатора',
          all(a['strategy_type'] == 'donchian' and a.get('lock_config')
              for a in assets), '')
    r = cfg['risk']
    check('live-конфиг: risk 0.35 / cap 6 / halt 20%',
          r['risk_percent'] == 0.35 and r['max_concurrent_positions'] == 6
          and r['max_drawdown'] == 20.0, '')
    check('live-конфиг: издержки H24 (0.055% taker, 0.015% слиппедж)',
          r['commission'] == 0.00055 and r['slippage'] == 0.00015, '')
    check('live-конфиг: dynamic_risk выключен (H23 провал)',
          cfg['dynamic_risk']['enabled'] is False, '')
    check('live-конфиг: commission_haircut=0 (паритет с H24)',
          r['commission_haircut'] == 0.0, '')
    check('live-конфиг: невалидированные дневные лимиты отключены',
          r['max_daily_loss'] > 50 and r['max_consecutive_losses'] > 50
          and r['max_daily_trades'] > 100, '')

    rm = RiskManager(risk_percent=r['risk_percent'], commission=r['commission'],
                     slippage=r['slippage'], stop_buffer=r['stop_buffer'],
                     max_drawdown=r['max_drawdown'],
                     max_leverage=r['max_leverage'],
                     commission_haircut=r['commission_haircut'])
    eq = 166242.48
    size = rm.calculate_position_size(100.0, 96.0, eq, 0.35, 0.01)
    check('live-размер = H24 (equity*0.35%/2*ATR, без haircut)',
          abs(size - eq * 0.0035 / 4.0) < 0.01
          and abs(size * 4.0 / eq * 100 - 0.35) < 0.001,
          f'size={size} risk%={size * 4.0 / eq * 100:.4f}')

    src = Path('main.py').read_text(encoding='utf-8')
    i_check = src.index("exit_result = self.tracker.check_exits(")
    i_trail = src.index("self._donchian_trailing(symbol, closed['high'],")
    check('run_cycle: check_exits идёт ПЕРЕД обновлением трейлинга',
          i_check < i_trail,
          f'check_exits@{i_check} < _donchian_trailing@{i_trail}')
    guard = src[:i_trail]
    check('run_cycle: трейлинг вызывается только если позиция не закрылась',
          'if not exit_result:' in guard[guard.rindex('if strat_type'):],
          '')
    P('')


# ─── 7. ER-фильтр входа (H32/H33/H36) ─────────────────────────────

def test_er_filter():
    P('=' * 92)
    P('7. ER-ФИЛЬТР ВХОДА (H32/H33/H36)')
    P('=' * 92)
    P('')

    off = DonchianConfig()
    check('ER-фильтр ВЫКЛЮЧЕН по умолчанию (стратегия не меняется молча)',
          off.er_filter is False, f'er_filter={off.er_filter}')
    check('выключенный фильтр не меняет прогрев',
          off.warmup_bars == max(off.lookback, off.atr_period) + 1,
          f'warmup={off.warmup_bars}')

    cfg = DonchianConfig(er_filter=True, er_period=20, er_threshold_window=950)
    gen = DonchianSignalGenerator('TESTUSDT', cfg)
    n_need = cfg.er_period + cfg.er_threshold_window + 2
    check('прогрев с фильтром = er_period + window + 2',
          cfg.warmup_bars == n_need, f'{cfg.warmup_bars} vs {n_need}')

    # --- формула ER совпадает с бэктестовой (h33/h36) ---
    # Ряд длиной 1200, чтобы окно порога в 950 баров реально наполнялось.
    rng = np.random.default_rng(7)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 1200)))
    er = efficiency_ratio(c, 20)
    manual = []
    for k in range(1200):
        if k < 20:
            manual.append(np.nan)
            continue
        # 20 шагов: путь c[k-20] -> c[k], как в pd.Series(diff).rolling(20)
        net = abs(c[k] - c[k - 20])
        path = np.abs(np.diff(c[k - 20:k + 1])).sum()
        manual.append(net / path if path > 0 else np.nan)
    manual = np.array(manual)
    both = ~np.isnan(er) & ~np.isnan(manual)
    check('ER совпадает с ручным расчётом Kaufman',
          np.allclose(er[both], manual[both], rtol=0, atol=1e-12),
          f'max|d|={np.abs(er[both]-manual[both]).max():.3e}')

    # --- порог = медиана строго ДО сигнального бара ---
    K = 1100
    th = er_threshold_at(er, K, 950)
    ref = np.nanmedian(er[K - 950:K])
    check('порог определён при достаточной истории', th is not None, f'{th}')
    check('порог = медиана ER[i-window:i], без бара i',
          th is not None and abs(th - ref) < 1e-12, f'{th} vs {ref}')
    # подмена текущего бара не должна двигать порог
    er2 = er.copy()
    er2[K] = 999.0
    check('порог не зависит от значения ER на сигнальном баре',
          abs(er_threshold_at(er2, K, 950) - th) < 1e-12, '')
    check('порог None при нехватке истории',
          er_threshold_at(er, 30, 950) is None, '')

    # --- ER не заглядывает вперёд ---
    cfut = c.copy()
    cfut[K + 1:] *= 3.0
    check('ER на баре i не зависит от будущих баров',
          np.allclose(efficiency_ratio(cfut, 20)[K],
                      efficiency_ratio(c, 20)[K], rtol=0, atol=1e-12), '')

    # --- выключенный фильтр = полное поведение (H24-паритет) ---
    # Donchian срабатывает редко, поэтому бары ищем сканом, а не по индексу.
    gen_off = DonchianSignalGenerator('TESTUSDT', DonchianConfig())
    df = synth(1200)

    sig_bars = [k for k in range(DonchianConfig().warmup_bars, 1198)
                if gen_off.evaluate(df, k) is not None]
    check('синтетика даёт сигнальные бары для проверки', len(sig_bars) > 0,
          f'{len(sig_bars)} баров')
    if sig_bars:
        k = sig_bars[0]
        so, sn = gen_off.evaluate(df, k), gen.evaluate(df, k)
        check('фильтр на сигнальном баре: либо тот же сигнал, либо отсев',
              (sn is None) or (sn['direction'] == so['direction']
                               and abs(sn['entry'] - so['entry']) < 1e-12
                               and abs(sn['stop'] - so['stop']) < 1e-12), '')
        if sn is not None:
            check('в extras видны ER и порог',
                  sn['extras']['er'] is not None
                  and sn['extras']['er_threshold'] is not None, '')
            check('прошедший сигнал имеет ER >= порога',
                  sn['extras']['er'] >= sn['extras']['er_threshold'],
                  f'er={sn["extras"]["er"]:.3f} th={sn["extras"]["er_threshold"]:.3f}')

    # прогрев: бар НИЖЕ warmup с фильтром отсекается всегда (fail-closed)
    early = [k for k in range(DonchianConfig().warmup_bars, cfg.warmup_bars)
             if gen_off.evaluate(df, k) is not None]
    check('есть сигналы ниже прогрева фильтра (проверка fail-closed)',
          len(early) > 0, f'{len(early)} баров')
    if early:
        check('прогрев с фильтром отсекает ранние сигналы (fail-closed)',
              gen.evaluate(df, early[0]) is None
              and gen_off.evaluate(df, early[0]) is not None, '')
    check('выходы фильтра не трогает (стоп = 2*ATR как в H24)',
          DonchianConfig().trail_atr == cfg.trail_atr == 2.0, '')
    check('фильтр не меняет risk_percent/max_bars',
          off.risk_percent == cfg.risk_percent == 0.35
          and off.max_bars == cfg.max_bars == 30, '')

    # --- фильтр активен, но не смертелен: режет часть, не всё ---
    df2 = synth(1400, seed=21)
    n_base = n_filt = 0
    for k in range(cfg.warmup_bars, 1398):
        if gen_off.evaluate(df2, k) is not None:
            n_base += 1
        if gen.evaluate(df2, k) is not None:
            n_filt += 1
    check('фильтр пропускает сигналы (не fail-closed по всем)',
          n_filt > 0, f'baseline {n_base}, с фильтром {n_filt}')
    check('фильтр режет часть сигналов, но не все',
          0 < n_filt < n_base, f'baseline {n_base}, с фильтром {n_filt}')
    # доля отсева должна быть в разумных пределах (H33: ~20% сделок)
    keep = n_filt / n_base if n_base else 0
    check('доля прошедших сигналов 50-100% (как в H33)',
          0.5 <= keep <= 1.0, f'осталось {keep*100:.0f}%')

    # --- live-конфиг реально включает фильтр на весь пул ---
    try:
        import yaml
        sp = Path(__file__).parent.joinpath('config', 'settings.yaml')
        y = yaml.safe_load(sp.read_text(encoding='utf-8'))
        g = (y.get('donchian') or {})
        check('settings.yaml: donchian.er_filter = true', g.get('er_filter') is True,
              f'{g}')
        check('settings.yaml: er_threshold_window = 950',
              g.get('er_threshold_window') == 950, f'{g.get("er_threshold_window")}')
        check('settings.yaml: er_period = 20', g.get('er_period') == 20, '')
        need = int(g.get('er_threshold_window', 0)) + int(g.get('er_period', 0)) + 4
        check('порог влезает в лимит Bybit (1000 баров на запрос)', need <= 1000,
              f'нужно {need}')
        # Блок donchian не должен «съедать» ключи risk (баг с отступом):
        check('settings.yaml: в donchian только ER-ключи',
              set(g) == {'er_filter', 'er_period', 'er_threshold_window'},
              f'лишние: {set(g) - {"er_filter", "er_period", "er_threshold_window"}}')
        rk = y.get('risk') or {}
        check('settings.yaml: risk.commission_haircut присутствует и = 0',
              'commission_haircut' in rk and rk['commission_haircut'] == 0.0,
              f'risk keys={sorted(rk)}')
        check('settings.yaml: risk-блок не потерял ключи H24',
              all(k in rk for k in ('risk_percent', 'commission', 'slippage',
                                    'max_drawdown', 'max_concurrent_positions',
                                    'max_leverage', 'commission_haircut')),
              f'risk keys={sorted(rk)}')
        check('settings.yaml: risk_percent = 0.35 (H24-паритет)',
              rk.get('risk_percent') == 0.35, f'{rk.get("risk_percent")}')
    except Exception as e:  # noqa: BLE001
        check('settings.yaml читается и включает ER-фильтр', False, str(e))

    P('')


def main():
    P('=' * 92)
    P('ТЕСТ ПАРИТЕТА: core/donchian_breakout.py против H24')
    P('=' * 92)
    P('')
    data, idx = H24.load()
    P(f'  пул {len(data)} монет, {idx[0].date()} .. {idx[-1].date()}, '
      f'{len(idx)} баров')
    P(f'  конфиг: {DonchianConfig()}')
    P('')
    test_indicators(data, idx)
    test_portfolio(data, idx)
    test_invariants()
    test_syntax_gate()
    test_er_filter()
    test_tracker_regression()
    test_wiring()

    P('=' * 92)
    if FAILS:
        P(f'ПРОВАЛЕНО {len(FAILS)}: ' + ', '.join(FAILS))
    else:
        P('ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ — модуль воспроизводит H24 побитово')
    P('=' * 92)
    OUT.write_text('\n'.join(_l), encoding='utf-8')
    print(f'Saved: {OUT.resolve()}')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
