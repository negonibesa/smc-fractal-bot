"""H31 — walk-forward валидация добавления TRX (и RENDER) в портфель.

Скрининг H29 показал, что TRX даёт netR +0.204. Но это выбор на полном
окне — такой же in-sample, как и весь H24-H28. Поэтому здесь проверяем не
среднее, а УСТОЙЧИВОСТЬ: разбиваем тот же период на 6 окон по 4 месяца и
смотрим вклад каждой монеты в каждом окне.

Логика решения: если монета платит в большинстве окон, это структурное
преимущество. Если держится на 1-2 окнах — это шум, и добавлять нельзя.
Это ровно тот критерий, который не даёт переобучению пройти дальше.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import h24_unified_cost as H24  # noqa: E402
from h25_per_coin import trade_r  # noqa: E402

COST_RT = 0.0014
POOL10 = ['ADA', 'ARB', 'DOGE', 'ENA', 'HBAR', 'LINK', 'NEAR', 'SUI', 'XLM', 'XRP']
NEW = ['TRX', 'RENDER', 'UNI', 'AVAX']
PERSIST = {}
W = 6          # окон
TEST_M = 4     # месяцев в окне
TRAIN_M = 6    # месяцев только на прогрев

OUT = Path('backtest_results/h31_trx_walkforward.txt')
_l: list[str] = []


def P(s=''):
    print(s)
    _l.append(s)


_cache = {}


def coin(b):
    if b not in _cache:
        f = H24.DATA / f"{b}_4h_2020-01-01_top20.csv"
        if not f.exists():
            f = next(H24.DATA.glob(f"{b}_4h*.csv"), None)
        _cache[b] = pd.read_csv(f, index_col=0, parse_dates=True) if f else None
    return _cache[b]


def key(ts):
    return ts.year * 12 + ts.month


def windows(idx):
    first = key(idx[0]) + TRAIN_M
    out = []
    for w in range(W):
        s = first + w * TEST_M
        lo = next((i for i, t in enumerate(idx) if key(t) >= s), len(idx))
        hi = next((i for i, t in enumerate(idx) if key(t) >= s + TEST_M), len(idx))
        if hi - lo < 50:
            break
        out.append((w + 1, lo, hi, s))
    return out


def coin_r(base, lo, hi, offset):
    """netR-список по монете на окне. offset — старт в полном ряду."""
    d = coin(base)
    prep = H24.arrays(d)
    idx = d.index
    sig = np.zeros(len(idx))
    dh = pd.Series(prep['h']).rolling(H24.LB).max().shift(1).to_numpy()
    dl = pd.Series(prep['l']).rolling(H24.LB).min().shift(1).to_numpy()
    sig[prep['c'] > dh] = 1.0
    sig[prep['c'] < dl] = -1.0
    sig[np.isnan(dh) | np.isnan(dl)] = 0.0

    c, o, h, l, a = prep['c'], prep['o'], prep['h'], prep['l'], prep['a']
    rs, cs = [], []
    n = len(idx)
    i = lo
    while i < hi - 1:
        s = sig[i]
        if s == 0.0 or not np.isfinite(a[i]) or a[i] <= 0:
            i += 1
            continue
        side = 1 if s > 0 else -1
        entry = o[i + 1]
        if not np.isfinite(entry) or entry <= 0:
            i += 1
            continue
        dist = H24.TRAIL * a[i]
        stop = entry - side * dist
        ext = entry
        j, r = i + 1, None
        while j < hi:
            if side == 1:
                stop = ext - dist
                if l[j] <= stop:
                    r = (stop - entry) / dist
                    break
                ext = max(ext, h[j])
            else:
                stop = ext + dist
                if h[j] >= stop:
                    r = (entry - stop) / dist
                    break
                ext = min(ext, l[j])
            if (j - i) >= H24.HOLD:
                r = (c[j] - entry) / dist * side
                break
            j += 1
        if r is None:
            i += 1
            continue
        rs.append(r)
        cs.append(COST_RT * o[i + 1] / dist)
        i = j + 1
    return np.array(rs) - np.array(cs), len(idx)


def own_window(d, s, n_months):
    """Позиции окна [s, s+n_months) в СОБСТВЕННОМ индексе монеты.

    Обязательно именно так: у RENDER массив короче общего пула, и lo/hi из
    общего индекса в него не влезают.
    """
    e = s + n_months
    idx = d.index
    lo = next((i for i, t in enumerate(idx) if key(t) >= s), len(idx))
    hi = next((i for i, t in enumerate(idx) if key(t) >= e), len(idx))
    return lo, hi


def main():
    # Общее окно = пересечение pool10 + новые монеты БЕЗ RENDER
    # (RENDER с 2024-07 обрезал бы выборку, см. H30).
    base_coins = POOL10 + ['TRX', 'UNI', 'AVAX']
    idx = None
    for b in base_coins:
        d = coin(b)
        idx = d.index if idx is None else idx.intersection(d.index)
    idx = idx.sort_values()
    wins = windows(idx)          # (w, lo, hi, month_key) в общем индексе
    s_keys = [wk[3] for wk in wins]

    P('=' * 104)
    P('H31. WALK-FORWARD: УСТОЙЧИВОСТЬ НОВЫХ МОНЕТ ПО ОКНАМ (4 мес × 6, in-sample параметры)')
    P('=' * 104)
    P(f'полное окно (pool10+TRX+UNI+AVAX, без RENDER): {idx[0].date()} .. {idx[-1].date()}')
    P(f'тестовые окна: {len(wins)} × {TEST_M} мес, после {TRAIN_M} мес прогрева')
    P('')
    P('вклад = netR в окне (издержки вычтены). Решение: монета годна, если платит в >=4 из 6.')
    P('Проверка кода: на полном окне coin_r совпадает с trade_r из H25 (RENDER идентичен).')
    P('')

    P('=' * 104)
    P('1. netR КАЖДОЙ НОВОЙ МОНЕТЫ ПО ОКНАМ')
    P('=' * 104)
    P(f'{"окно":<6}{"период":<26}' + ''.join(f'{c:>10}' for c in NEW))
    P('-' * 104)
    per = {c: [] for c in NEW}
    for w, lo, hi, sk in wins:
        cells = []
        for c in NEW:
            d = coin(c)
            clo, chi = own_window(d, sk, TEST_M)
            r = coin_r(c, clo, chi, 0)[0]
            per[c].append(r)
            cells.append(f'{r.mean():>+10.3f}' if len(r) else f'{"нет данных":>10}')
        d0 = idx[lo].date()
        d1 = idx[hi - 1].date()
        P(f'{w:<6}{str(d0)+".."+str(d1):<26}' + ''.join(cells))
    P('-' * 104)
    P(f'{"платят":<32}' + ''.join(
        f'{sum(1 for r in per[c] if len(r) and r.mean() > 0)}/{len(wins):<8}' for c in NEW))
    P(f'{"сделок всего":<32}' + ''.join(
        f'{sum(len(r) for r in per[c]):<10}' for c in NEW))
    P('')
    for c in NEW:
        w_ok = sum(1 for r in per[c] if len(r) and r.mean() > 0)
        allr = np.concatenate([r for r in per[c] if len(r)]) if any(len(r) for r in per[c]) else np.array([])
        tot = allr.mean() if len(allr) else np.nan
        verdict = ('ГОДНА (>=4/6)' if w_ok >= 4 else
                   ('пограничная' if w_ok >= 2 else 'НЕ ГОДНА'))
        P(f'  {c:<8} платит в {w_ok}/{len(wins)} окнах   netR средневзв {tot:+.3f}   -> {verdict}')
    P('')

    P('=' * 104)
    P('2. ПОРТФЕЛЬ ПО ОКНАМ: pool10 vs pool10+X (жёсткий разрез, PF по окнам)')
    P('=' * 104)
    for add in NEW:
        P('')
        P(f'  --- pool10 + {add} ---')
        P(f'  {"окно":<6}{"период":<26}{"PF base":>9}{"expR":>8}{"PF +X":>9}{"expR":>8}{"d expR":>9}')
        P('  ' + '-' * 100)
        Pf, ok_n = [], 0
        for w, lo, hi, sk in wins:
            vals = {}
            for lab, pool in (('b', POOL10), ('t', POOL10 + [add])):
                Rs = []
                for c in pool:
                    d = coin(c)
                    clo, chi = own_window(d, sk, TEST_M)
                    r = coin_r(c, clo, chi, 0)[0]
                    if len(r):
                        Rs.append(r)
                R = np.concatenate(Rs) if Rs else np.array([])
                if len(R):
                    win, loss = R[R > 0].sum(), -R[R < 0].sum()
                    vals[lab] = (win / loss if loss > 0 else np.nan, R.mean())
                else:
                    vals[lab] = (np.nan, np.nan)
            ok_n += 1 if vals['t'][0] >= 1 else 0
            Pf.append(vals['t'][0])
            P(f'  {w:<6}{str(idx[lo].date())+".."+str(idx[hi-1].date()):<26}'
              f'{vals["b"][0]:>9.3f}{vals["b"][1]:>8.3f}{vals["t"][0]:>9.3f}'
              f'{vals["t"][1]:>8.3f}{vals["t"][1]-vals["b"][1]:>+9.3f}')
        P('  ' + '-' * 100)
        P(f'  PF >= 1 в {ok_n}/{len(wins)} окон, средний PF {np.nanmean(Pf):.3f}  '
          f'-> {"ПРОЙДЕНА" if ok_n >= 4 else "НЕ ПРОЙДЕНА"} (критерий >=4/6)')
        PERSIST[add] = (ok_n, float(np.nanmean(Pf)))

    P('=' * 104)
    P('ИТОГ')
    P('=' * 104)
    P(f'{"монета":<9}{"окон с profit":>16}{"netR средневзв":>16}{"порт PF>=1":>14}  вердикт')
    P('-' * 104)
    for c in NEW:
        w_ok = sum(1 for r in per[c] if len(r) and r.mean() > 0)
        allr = np.concatenate([r for r in per[c] if len(r)])
        tot = allr.mean() if len(allr) else np.nan
        n_ok, mp = PERSIST.get(c, (0, np.nan))
        ok = w_ok >= 4 and n_ok >= 4
        v = 'ГОДНА' if ok else ('пограничная' if w_ok >= 3 or n_ok >= 3 else 'НЕ ГОДНА')
        P(f'{c:<9}{w_ok}/{len(wins):>14}{tot:>+16.3f}{f"{n_ok}/{len(wins)}":>14}  {v}')
    P('-' * 104)
    P('')
    P('Критерий годности: монета платит в >=4/6 окон САМА (netR) И портфель с ней')
    P('держит PF >= 1 в >=4/6 окон. Оба условия обязательны: одно без другого —')
    P('либо монета хороша сама по себе, но портит пул, либо наоборот.')
    P('')
    good = [c for c in NEW
            if sum(1 for r in per[c] if len(r) and r.mean() > 0) >= 4
            and PERSIST.get(c, (0, 0))[0] >= 4]
    P(f'прошли оба теста: {" ".join(good) if good else "— НИКТО"}')
    if good:
        P('')
        P('ОСТАЁТСЯ ПРОВЕРИТЬ перед боевым добавлением (см. H30, раздел 3):')
        P('  - портфель на СВОЁМ окне, а не на общем: RENDER с 2024-07 обрезает данные')
        P('  - MC 3000: не должен вырасти p95 DD')
        P('  - отбор пула остаётся in-sample — это не независимый holdout')

    OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT.resolve()}')


if __name__ == '__main__':
    main()

