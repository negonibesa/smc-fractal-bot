"""H29 — скрининг 10 кандидатов в пул Donchian.

Ключевое отличие от H25: НЕ пересекаем индексы всех монет. HYPE торгуется
с 2024-12, RENDER — с 2024-07, и общее пересечение урезало бы весь анализ
до ~21 месяца, сломав сопоставимость с H26 (2024-04..2026-09).

Здесь каждая монета оценивается на своём собственном окне, а эталонное
окно берётся из пересечения ТЕКУЩЕГО пула pool10 — то есть ровно то, на
котором считались H24/H26/H28. Монеты с укороченной историей помечаются
явно: их выборка не сравнима с полной, и это нельзя игнорировать.

Параметры зафиксированы на H24 (lb=20, ATR20, trail=2.0, hold=30).
Портфельный cap НЕ применяется — это независимый вклад каждой монеты.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import h24_unified_cost as H24  # noqa: E402
from h25_per_coin import trade_r  # noqa: E402

COST_RT = 0.0014  # taker 0.055% + slippage 0.015% на сторону
POOL10 = ['ADA', 'ARB', 'DOGE', 'ENA', 'HBAR', 'LINK', 'NEAR', 'SUI', 'XLM', 'XRP']
CANDIDATES = ['ONDO', 'BTC', 'HYPE', 'BNB', 'TRX', 'RENDER', 'SOL', 'XMR', 'UNI', 'AVAX']

OUT = Path('backtest_results/h29_candidates.txt')
_l: list[str] = []


def P(s=''):
    print(s)
    _l.append(s)


def load_coin(base):
    f = H24.DATA / f"{base}_4h_2020-01-01_top20.csv"
    if not f.exists():
        f = next(H24.DATA.glob(f"{base}_4h*.csv"), None)
    if f is None:
        return None
    return pd.read_csv(f, index_col=0, parse_dates=True)


def eval_coin(base, lo, hi):
    d = load_coin(base)
    if d is None:
        return None
    d = d.loc[lo:hi]
    if len(d) < 200:
        return None
    prep = H24.arrays(d)
    rs = trade_r(prep, d.index, H24.LB, H24.ATRN, H24.TRAIL, H24.HOLD)
    if not rs:
        return None
    r = np.array([x[2] for x in rs])
    cost = np.array([COST_RT * prep['o'][x[4]] / x[3] for x in rs])
    net = r - cost
    losses = r[r <= 0]
    pf = (r[r > 0].sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else np.inf
    curve = np.cumsum(net)
    peak = np.maximum.accumulate(np.concatenate([[0.0], curve]))[1:]
    return dict(coin=base, n=len(r), wr=float((r > 0).mean() * 100), exp=float(r.mean()),
                pf=float(pf), net=float(net.mean()), cost=float(cost.mean()),
                dd=float((peak - curve).max()), tot=float(net.sum()),
                gross=float(r.sum()), sd=float(r.std()),
                d0=d.index[0], d1=d.index[-1], nbars=len(d))


def main():
    # эталонное окно = пересечение текущего пула pool10 (= окно H26)
    ref = [load_coin(b) for b in POOL10]
    ref = [d for d in ref if d is not None]
    idx = ref[0].index
    for d in ref[1:]:
        idx = idx.intersection(d.index)
    lo, hi = idx.min(), idx.max()

    P('=' * 108)
    P('H29. СКРИНИНГ КАНДИДАТОВ В ПУЛ (Donchian 20, параметры H24, netR после издержек)')
    P('=' * 108)
    P(f'эталонное окно (пересечение текущего pool10) = окно H26: '
      f'{lo.date()} .. {hi.date()}  ({len(idx)} баров 4H)')
    P(f'параметры: lb={H24.LB} ATR={H24.ATRN} trail={H24.TRAIL}x hold={H24.HOLD}')
    P(f'издержки: {COST_RT * 100:.2f}% round-trip, переведены в R по каждой сделке')
    P('cap портфеля НЕ применяется — независимый вклад монеты')
    P('')

    def block(title, coins, note=''):
        P('-' * 108)
        P(title)
        if note:
            P(f'  {note}')
        P(f'{"монета":<8}{"сделок":>7}{"WR%":>7}{"expR":>8}{"PF":>7}{"netR":>8}'
          f'{"DD_R":>7}{"суммаR":>9}{"costR":>7}{"покрытие":>12}  статус')
        P('-' * 108)
        res = []
        for c in coins:
            e = eval_coin(c, lo, hi)
            if e is None:
                P(f'{c:<8}{"нет данных":>28}')
                continue
            res.append(e)
        for e in sorted(res, key=lambda x: -x['net']):
            cover = (e['d1'] - e['d0']).days / (hi - lo).days * 100
            pfs = 'inf' if e['pf'] == np.inf else f"{e['pf']:.2f}"
            short = cover < 95
            if e['net'] <= 0:
                st = 'НЕ ПЛАТИТ издержки'
            elif e['net'] < 0.05:
                st = 'пограничный'
            else:
                st = 'ok'
            if short:
                st += ' !короткая история'
            P(f'{e["coin"]:<8}{e["n"]:>7}{e["wr"]:>7.1f}{e["exp"]:>8.3f}{pfs:>7}'
              f'{e["net"]:>8.3f}{e["dd"]:>7.2f}{e["tot"]:>9.1f}{e["cost"]:>7.3f}'
              f'{cover:>11.0f}%  {st}')
        P('-' * 108)
        return res

    cand = block('КАНДИДАТЫ (запрошенные 10)', CANDIDATES)
    base = block('ТЕКУЩИЙ ПУЛ pool10 (эталон, те же параметры)', POOL10)

    P('')
    P('=' * 108)
    P('ВЫВОДЫ')
    P('=' * 108)
    pay = [e['coin'] for e in cand if e['net'] > 0.05]
    marg = [e['coin'] for e in cand if 0 < e['net'] <= 0.05]
    dead = [e['coin'] for e in cand if e['net'] <= 0]
    P(f'платят издержки с запасом (netR > 0.05, {len(pay)}): {" ".join(sorted(pay)) or "—"}')
    P(f'пограничные   (0 < netR <= 0.05, {len(marg)}): {" ".join(sorted(marg)) or "—"}')
    P(f'не платят     (netR <= 0, {len(dead)}): {" ".join(sorted(dead)) or "—"}')
    P('')
    bmed = float(np.median([e['net'] for e in base]))
    better = [e['coin'] for e in cand if e['net'] > bmed]
    P(f'медиана netR по pool10 = {bmed:+.3f} (порог «лучше медианы текущего пула»)')
    P(f'кандидаты лучше медианы пула ({len(better)}): {" ".join(sorted(better)) or "—"}')
    P('')
    short = [e for e in cand if (e['d1'] - e['d0']).days / (hi - lo).days < 0.95]
    if short:
        P('ВНИМАНИЕ — укороченная история (выборка не сравнима с полной, вердикт предварительный):')
        for e in short:
            cov = (e['d1'] - e['d0']).days / (hi - lo).days * 100
            P(f'  {e["coin"]:<8} данные с {e["d0"].date()}  покрытие {cov:.0f}%  '
              f'сделок {e["n"]}  netR {e["net"]:+.3f}')
        P('')
    P('ВАЖНО: портфельный эффект не учитывается. Монета с положительным netR может')
    P('ухудшить портфель (корреляция, cannibalization лимита cap=6). Решение о добавлении')
    P('принимается только после прогона портфеля и walk-forward.')

    OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT.resolve()}')


if __name__ == '__main__':
    main()
