# -*- coding: utf-8 -*-
"""H24 - ЕДИНАЯ КОНВЕНЦИЯ ИЗДЕРЖЕК.

Проблема, найденная в h21_portfolio.py:
    entry = open * (1 + SLIP) * (1 + TAKER)   # комиссия внутри цены
    cash -= qty * entry * TAKER               # комиссия второй раз
    ... и то же на выходе.
Комиссия списывалась дважды на каждой стороне. Trade-level PF при этом
считался по ценам и был верен - поэтому PF и доходность расходились.

Здесь издержки применяются РОВНО ОДИН РАЗ и явно:
    цена входа/выхода = цена бара x (1 +/- SLIP)     только проскальзывание
    комиссия          = qty * цена * TAKER            отдельно, в деньгах
    PnL               = qty*(выход-вход)*side - fee_in - fee_out
    R                 = PnL / (qty * risk_per_unit)

Эквити и R по построению согласованы, комиссию нельзя потерять или
удвоить. Отдельно проверяется, на что считается размер: на cash или
на полную эквити с нереализованным PnL.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

DATA = Path('data/raw')
TAKER, SLIP = 0.00055, 0.00015
LB, ATRN, TRAIL, HOLD = 20, 20, 2.0, 30
CAPITAL = 166_242.48
LEV, MARGIN_BUF = 10.0, 0.80
EXCLUDE_14 = {'XMR', 'LTC', 'BTC'}
# Кандидаты H29. Лежат в data/raw рядом с боевыми, поэтому load() обязан их
# отсекать: иначе пересечение индексов схлопнется до начала истории HYPE
# (2024-12) и ВСЕ легаси-расчёты молча поедут. Включаются только явно.
NEW_COINS = {'ONDO', 'HYPE', 'BNB', 'TRX', 'RENDER', 'UNI', 'AVAX'}
POOL_PIN = None      # если задан — ровно этот пул, независимо от data/raw
CAP = 6
RISKS = (0.0025, 0.0035, 0.005, 0.0075)
N_ITER = 3000
BLOCK = 50
SEED = 20260926

OUT = Path('backtest_results/h24_unified_cost.txt')
_l: list[str] = []


def P(s=''):
    print(s)
    _l.append(s)


def true_range(o, h, l, c):
    pc = np.r_[np.nan, c[:-1]]
    return np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)


def load():
    if POOL_PIN is not None:
        pool = set(POOL_PIN)
    else:
        pool = {f.name.split('_4h')[0] for f in DATA.glob('*_4h*.csv')} \
            - EXCLUDE_14 - NEW_COINS
    data = {}
    for f in sorted(DATA.glob('*_4h*.csv')):
        b = f.name.split('_4h')[0]
        if b in pool:
            data[b] = pd.read_csv(f, index_col=0, parse_dates=True)
    lo = max(d.index.min() for d in data.values())
    hi = max(d.index.max() for d in data.values())
    data = {k: v.loc[lo:hi] for k, v in data.items()}
    idx = None
    for d in data.values():
        idx = d.index if idx is None else idx.intersection(d.index)
    out = {}
    for k, v in data.items():
        dd = v.reindex(idx).dropna()
        if len(dd) > 200:
            out[k] = dd
    return out, idx


def arrays(d):
    o = d['open'].to_numpy(float)
    c = d['close'].to_numpy(float)
    h = d['high'].to_numpy(float)
    l = d['low'].to_numpy(float)
    a = pd.Series(true_range(o, h, l, c)).rolling(ATRN).mean().to_numpy()
    dh = pd.Series(h).rolling(LB).max().shift(1).to_numpy()
    dl = pd.Series(l).rolling(LB).min().shift(1).to_numpy()
    sig = np.zeros(len(d))
    sig[c > dh] = 1.0
    sig[c < dl] = -1.0
    sig[np.isnan(dh)] = 0.0
    return dict(o=o, c=c, h=h, l=l, a=a, sig=sig)


def run(prep, idx, risk, cap=CAP, size_on='equity', capital=CAPITAL,
        fee_twice=False, entry_mask=None):
    """size_on: 'equity' (риск от полной эквити) | 'cash' (как в H21)
    fee_twice: True воспроизводит баг H21 для оценки масштаба ошибки.
    entry_mask: {co: bool[j]} — разрешён ли вход по сигналу бара j (H33).
                 None = без фильтра. Метка берётся на СИГНАЛЬНОМ баре, вход
                 на следующем, поэтому внутри цикла проверяем j-1.
    """
    n = len(idx)
    coins = sorted(prep)
    cash = capital
    openp, pend = {}, {}
    eq = np.empty(n)
    used = np.zeros(n, bool)
    R = []
    # индекс бара выхода и символ каждой закрытой сделки — для помесячной
    # разбивки в H27. На существующие метрики не влияет.
    R_bar = []
    R_sym = []
    R_ei = []

    for j in range(n):
        # --- входы на открытии бара
        for co, s in list(pend.items()):
            p = prep[co]
            if entry_mask is not None:
                # j — бар входа, метка фильтра — на сигнальном баре j-1.
                # Отклонённый сигнал ОТМЕНЯЕТСЯ, а не откладывается.
                k = j - 1
                ok = (k >= 0 and entry_mask[co][k])
                if not ok:
                    pend.pop(co, None)
                    continue
            ae = p['a'][j]
            if np.isnan(ae) or ae <= 0:
                continue
            rpu = TRAIL * ae                       # риск на единицу
            if rpu <= 0:
                continue
            base = p['o'][j]
            entry = base * (1 + SLIP) * ((1 + TAKER) if fee_twice else 1.0)
            cur = cash + sum(q['qty'] * (prep[k]['c'][j] - q['entry']) * q['side']
                             for k, q in openp.items())
            qty = ((cur if size_on == 'equity' else cash) * risk) / rpu
            notional = qty * entry
            marg = sum(q['qty'] * prep[k]['c'][j] for k, q in openp.items())
            if cap and len(openp) >= cap:
                continue
            if marg + notional > cur * LEV * MARGIN_BUF:
                continue
            # комиссия на входе списывается ВСЕГДА и ровно один раз
            cash -= qty * entry * TAKER
            openp[co] = dict(side=s, entry=entry, qty=qty,
                             stop=entry - s * TRAIL * ae, peak=entry, ei=j)
            used[j] = True
        pend.clear()

        # --- стоп ДО обновления экстремума
        for co, q in list(openp.items()):
            p = prep[co]
            hit = (p['l'][j] <= q['stop']) if q['side'] == 1 \
                else (p['h'][j] >= q['stop'])
            timed = (j - q['ei'] >= HOLD)
            if not (hit or timed):
                if q['side'] == 1:
                    q['peak'] = max(q['peak'], p['h'][j])
                    q['stop'] = max(q['stop'], q['peak'] - TRAIL * p['a'][q['ei']])
                else:
                    q['peak'] = min(q['peak'], p['l'][j])
                    q['stop'] = min(q['stop'], q['peak'] + TRAIL * p['a'][q['ei']])
                continue

            raw = q['stop'] if hit else p['c'][j]
            px = raw * (1 - SLIP) * ((1 - TAKER) if fee_twice else 1.0)
            pnl = (q['qty'] * (px - q['entry']) * q['side']) - q['qty'] * px * TAKER
            cash += pnl
            risk_dollars = q['qty'] * TRAIL * p['a'][q['ei']]
            R.append(pnl / risk_dollars if risk_dollars > 0 else 0.0)
            R_bar.append(j)
            R_sym.append(co)
            # бар входа — для разбивки сделок по режиму рынка (H32).
            # Режим фильтруется ДО входа, поэтому метка нужна на ei, а не на j.
            R_ei.append(q['ei'])
            del openp[co]

        e = cash
        for co, q in openp.items():
            e += q['qty'] * (prep[co]['c'][j] - q['entry']) * q['side']
        eq[j] = e

        if j + 1 < n:
            for co in coins:
                if co in openp or co in pend:
                    continue
                s = prep[co]['sig'][j]
                if s != 0:
                    pend[co] = s

    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak
    R = np.array(R)
    w, l = R[R > 0].sum(), -R[R < 0].sum()
    return dict(tot=float(eq[-1] / capital - 1), mdd=float(dd.max()),
                n_tr=len(R), exposure=float(used.mean()),
                pf=float(w / l) if l > 0 else np.nan,
                exp_r=float(R.mean()) if len(R) else np.nan,
                wr=float((R > 0).mean()) if len(R) else np.nan,
                eq=eq, idx=idx, dd=dd, R=R,
                R_bar=np.array(R_bar, dtype=int),
                R_sym=np.array(R_sym, dtype=object),
                R_ei=np.array(R_ei, dtype=int))


def mc(R, risk, n_iter=N_ITER, block=BLOCK, seed=SEED):
    rng = np.random.default_rng(seed)
    L = len(R)
    starts = np.arange(0, L - block + 1)
    nb = int(np.ceil(L / block))
    tots, mdds = [], []
    for _ in range(n_iter):
        pick = rng.integers(0, len(starts), nb)
        path = np.concatenate([R[s:s + block] for s in starts[pick]])[:L]
        e = np.cumprod(1 + risk * path)
        pk = np.maximum.accumulate(e)
        d = (pk - e) / pk
        tots.append(e[-1] - 1)
        mdds.append(d.max())
    return np.array(tots), np.array(mdds)


def main():
    P('=' * 100)
    P('H24 - ЕДИНАЯ КОНВЕНЦИЯ ИЗДЕРЖЕК (исправление H21)')
    P('=' * 100)
    P('  комиссия taker и проскальзывание применяются РОВНО ОДИН РАЗ:')
    P('    вход/выход = цена бара x (1 +/- слип)      только слип')
    P('    комиссия    = qty x цена x taker           отдельно в деньгах')
    P(f'  taker {TAKER:.3%}, слип {SLIP:.3%} на сторону, итого '
      f'{2*(TAKER+SLIP):.3%} на круг')
    P('')

    data, idx = load()
    prep = {k: arrays(v) for k, v in data.items()}
    months = (idx[-1] - idx[0]).days / 30.44
    P(f'  пул {len(prep)} монет, {idx[0].date()} .. {idx[-1].date()}, '
      f'{len(idx)} баров, {months:.0f} мес')
    P('')

    P('=' * 100)
    P('1. МАСШТАБ ОШИБКИ (риск 0.50%, лимит 6)')
    P('=' * 100)
    P('')
    P(f'  {"учёт":34s}{"сделок":>8s}{"итог":>11s}{"просадка":>11s}'
      f'{"PF":>7s}{"exp, R":>9s}')
    P('  ' + '-' * 82)
    cells = (('комиссия x2, размер от cash  (H21)', 'cash', True),
             ('комиссия x2, размер от эквити', 'equity', True),
             ('комиссия x1, размер от cash', 'cash', False),
             ('комиссия x1, размер от эквити  (верно)', 'equity', False))
    ref = None
    for nm, so, ft in cells:
        r = run(prep, idx, 0.005, size_on=so, fee_twice=ft)
        P(f'  {nm:34s}{r["n_tr"]:8d}{r["tot"]:11.1%}{r["mdd"]:11.1%}'
          f'{r["pf"]:7.2f}{r["exp_r"]:+9.4f}')
        if not ft and so == 'equity':
            ref = r
    a = run(prep, idx, 0.005, size_on='cash', fee_twice=True)
    P('')
    P(f'  ошибка H21 занижала итог на {a["tot"] - ref["tot"]:+.1%} п.п. '
      f'и завышала просадку на {a["mdd"] - ref["mdd"]:+.1%} п.п.')
    P(f'  ошибка в размере (cash вместо эквити): '
      f'{run(prep,idx,0.005,size_on="cash",fee_twice=False)["tot"] - ref["tot"]:+.1%} п.п.')
    P('')

    P('=' * 100)
    P('2. ИСПРАВЛЕННЫЕ РИСКИ (комиссия x1, размер от эквити, лимит 6)')
    P('=' * 100)
    P('')
    P(f'  {"риск":>7s}{"сделок":>8s}{"итог":>11s}{"просадка":>11s}'
      f'{"PF":>7s}{"exp, R":>9s}{"WR":>7s}{"в рынке":>10s}{"Calmar":>8s}')
    P('  ' + '-' * 80)
    Rs = {}
    for risk in RISKS:
        r = run(prep, idx, risk)
        Rs[risk] = r
        P(f'  {risk:7.2%}{r["n_tr"]:8d}{r["tot"]:11.1%}{r["mdd"]:11.1%}'
          f'{r["pf"]:7.2f}{r["exp_r"]:+9.4f}{r["wr"]:7.1%}'
          f'{r["exposure"]:10.1%}{r["tot"]/max(r["mdd"],1e-9):8.2f}')
    P('')
    base = Rs[RISKS[0]]['R']
    same = all(np.allclose(Rs[k]['R'], base) for k in RISKS)
    P(f'  R-последовательность одинакова для всех рисков: {same}')
    if not same:
        P('  (!) набор сделок зависит от риска - Монте-Карло ниже неприменим')
    P('')

    P('=' * 100)
    P(f'3. МОНТЕ-КАРЛО на исправленной R ({N_ITER} ит., блок {BLOCK})')
    P('=' * 100)
    P('')
    P(f'  {"риск":>7s}{"дох. p5":>10s}{"медиана":>10s}{"p95":>10s}'
      f'{"DD p50":>9s}{"DD p95":>9s}{"P(DD>30)":>10s}{"P(DD>40)":>10s}'
      f'{"Calmar p50":>12s}')
    P('  ' + '-' * 90)
    for risk in RISKS:
        t, d = mc(base, risk)
        cal = t / np.maximum(d, 1e-9)
        P(f'  {risk:7.2%}{np.percentile(t,5):10.1%}{np.percentile(t,50):10.1%}'
          f'{np.percentile(t,95):10.1%}{np.percentile(d,50):9.1%}'
          f'{np.percentile(d,95):9.1%}{np.mean(d>0.30):10.1%}'
          f'{np.mean(d>0.40):10.1%}{np.percentile(cal,50):12.2f}')
    P('')

    P('=' * 100)
    P('4. ПО ГОДАМ')
    P('=' * 100)
    P('')
    for risk in RISKS:
        r = Rs[risk]
        P(f'  риск {risk:.2%}:')
        for yr in (2024, 2025, 2026):
            m = np.where(idx.year == yr)[0]
            if not len(m):
                continue
            e0 = r['eq'][m[0] - 1] if m[0] > 0 else CAPITAL
            P(f'    {yr}: {r["eq"][m[-1]]/e0 - 1:+7.1%}   '
              f'макс.просадка {r["dd"][m].max():6.1%}   баров {len(m)}')
        P('')

    P('=' * 100)
    P('5. ПРОВЕРКА: комиссия действительно одна')
    P('=' * 100)
    P('')
    rr = ref['R']
    P(f'  сделок {len(rr)}, средний R {rr.mean():+.4f}, WR {(rr>0).mean():.1%}')
    P(f'  худший R {rr.min():.2f} (с тейком может быть чуть хуже -1.00), '
      f'лучший {rr.max():+.2f}')
    if len(rr) > 10:
        z = np.r_[0.0, (rr[:-1] == 0) & (rr[1:] != 0)]
        P(f'  доля сделок строго между -1 и 0: '
          f'{np.mean((rr < 0) & (rr > -1.02)):.1%}')
    P('  если бы комиссия считалась дважды, средний R был бы систематически')
    P('  ниже на ~2 x taker x notional/risk; сверка с H21 выше это показывает.')
    P('')

    OUT.write_text('\n'.join(_l), encoding='utf-8')
    P(f'Saved: {OUT.resolve()}')


if __name__ == '__main__':
    main()
