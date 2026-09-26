"""H25 — побитовая разбивка валидированной стратегии по монетам.

Цель: не выбрать новый пул, а показать вклад каждой монеты в H24.
Параметры зафиксированы на H24 (lb=20, ATR20, trail=2, hold=30) — ничего
не тюнится, меняется только набор монет.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import h24_unified_cost as H24  # noqa: E402

OUT = Path('backtest_results/h25_per_coin.txt')
_l: list[str] = []


def P(s=''):
    print(s)
    _l.append(s)


def trade_r(prep, idx, lb=20, atrn=20, trail=2.0, hold=30):
    """Список доходностей в R по одной монете (без комиссий и cap)."""
    o, c, h, l = prep['o'], prep['c'], prep['h'], prep['l']
    a = prep['a']
    dh = pd.Series(h).rolling(lb).max().shift(1).to_numpy()
    dl = pd.Series(l).rolling(lb).min().shift(1).to_numpy()
    sig = np.zeros(len(c))
    sig[c > dh] = 1.0
    sig[c < dl] = -1.0
    sig[np.isnan(dh) | np.isnan(dl)] = 0.0

    rs = []
    i = 0
    n = len(c)
    while i < n - 1:
        s = sig[i]
        if s == 0.0 or not np.isfinite(a[i]) or a[i] <= 0:
            i += 1
            continue
        side = 1 if s > 0 else -1
        entry = o[i + 1]
        if not np.isfinite(entry) or entry <= 0:
            i += 1
            continue
        dist = trail * a[i]
        stop = entry - side * dist
        ext = entry
        j = i + 1
        r = None
        while j < n:
            if side == 1:
                stop_t = stop < ext - dist + 1e-12
                stop = ext - dist
                if l[j] <= stop:
                    r = (stop - entry) / dist
                    break
                if h[j] > ext:
                    ext = h[j]
            else:
                stop = ext + dist
                if h[j] >= stop:
                    r = (entry - stop) / dist
                    break
                if l[j] < ext:
                    ext = l[j]
            if (j - (i + 1) + 1) >= hold:
                r = (c[j] - entry) / dist * side
                break
            j += 1
        if r is None:
            i += 1
            continue
        rs.append((idx[i], side, r, dist, i + 1))
        i = j + 1
    return rs


def load_all():
    """Все монеты с данными, включая исключённые в H20 (XMR/LTC/BTC)."""
    data = {}
    for f in sorted(H24.DATA.glob('*_4h*.csv')):
        b = f.name.split('_4h')[0]
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


COST_RT = 0.0014  # taker 0.055% + slippage 0.015% на сторону


def main():
    data, idx = load_all()
    lb, atrn, trail, hold = H24.LB, H24.ATRN, H24.TRAIL, H24.HOLD
    P('=' * 106)
    P('H25. ПОБИТОВАЯ РАЗБИВКА ПО МОНЕТАМ (параметры H24, портфельный cap НЕ применяется)')
    P('=' * 106)
    P(f'период: {idx[0].date()} .. {idx[-1].date()}  ({len(idx)} баров 4H)')
    P(f'параметры: lb={lb} ATR={atrn} trail={trail}x hold={hold}')
    P(f'издержки: {COST_RT*100:.2f}% round-trip, переведены в R по каждой сделке')
    P('')

    rows = []
    all_r = []
    all_cost = []
    for coin in sorted(data):
        prep = H24.arrays(data[coin])
        rs = trade_r(prep, idx, lb, atrn, trail, hold)
        if not rs:
            continue
        r = np.array([x[2] for x in rs])
        # стоимость в R = 0.14% от номинала / риск-юнит (2*ATR на баре входа)
        cost = np.array([COST_RT * prep['o'][x[4]] / x[3] for x in rs])
        all_r.extend(r)
        all_cost.extend(cost)
        net = r - cost
        wins = r[r > 0]
        losses = r[r <= 0]
        pf = (wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else np.inf
        curve = np.cumsum(net)
        peak = np.maximum.accumulate(np.concatenate([[0.0], curve]))[1:]
        dd = (peak - curve)
        rows.append((coin, len(r), float((r > 0).mean() * 100), float(r.mean()),
                     float(pf), float(dd.max()), float(r.sum()),
                     float(abs(losses.sum()) if len(losses) else 0.0),
                     float(r.std()), float(net.mean()), float(cost.mean())))

    P(f'{"монета":<8}{"сделок":>7}{"WR%":>7}{"expR":>8}{"PF":>7}{"netR":>8}'
      f'{"DD_R":>7}{"суммаR":>9}{"costR":>7}{"статус":>22}')
    P('-' * 106)
    for row in sorted(rows, key=lambda x: -x[9]):
        coin, n, wr, exp, pf, dd, tot, lossabs, sd, net, cost = row
        pfs = 'inf' if pf == np.inf else f'{pf:.2f}'
        status = 'НЕ ПЛАТИТ ИЗДЕРЖКИ' if net <= 0 else ('слабый' if net < 0.05 else 'ok')
        P(f'{coin:<8}{n:>7}{wr:>7.1f}{exp:>8.3f}{pfs:>7}{net:>8.3f}'
          f'{dd:>7.2f}{tot:>9.1f}{cost:>7.3f}{status:>22}')
    P('-' * 106)
    allr = np.array(all_r)
    allc = np.array(all_cost)
    P(f'{"ВСЕГО":<8}{len(allr):>7}{(allr > 0).mean() * 100:>7.1f}'
      f'{allr.mean():>8.3f}{"":>7}{(allr - allc).mean():>8.3f}{"":>7}'
      f'{allr.sum():>9.1f}{allc.mean():>7.3f}')
    P('')

    pay = [r[0] for r in rows if r[9] > 0.05]
    marginal = [r[0] for r in rows if 0 < r[9] <= 0.05]
    dead = [r[0] for r in rows if r[9] <= 0]
    P(f'ПЛАТЯТ издержки с запасом (netR > 0.05, {len(pay)}): {" ".join(sorted(pay))}')
    P(f'Пограничные (0 < netR <= 0.05, {len(marginal)}): {" ".join(sorted(marginal))}')
    P(f'НЕ платят издержки (netR <= 0, {len(dead)}): {" ".join(sorted(dead))}')
    P('')
    excl = [r for r in rows if r[0] in H24.EXCLUDE_14]
    if excl:
        P('--- исключённые в H20: стоит ли вернуть? ---')
        for row in sorted(excl, key=lambda x: -x[9]):
            coin, n, wr, exp, pf, dd, tot, lossabs, sd, net, cost = row
            pfs = 'inf' if pf == np.inf else f'{pf:.2f}'
            verdict = 'вернуть' if net > 0.05 else ('пограничный' if net > 0 else 'нет')
            P(f'{coin:<8}{n:>7}{wr:>7.1f}{exp:>8.3f}{pfs:>7}{net:>8.3f}'
              f'{dd:>7.2f}{tot:>9.1f}{cost:>7.3f}{verdict:>22}')
    P('')
    P('--- вклад в портфель: доля сделок и доля суммарного R ---')
    P(f'{"монета":<8}{"% сделок":>10}{"% суммы R":>12}{"% убытков":>12}')
    P('-' * 42)
    tot_n = sum(r[1] for r in rows)
    tot_sum = sum(r[6] for r in rows)
    tot_loss = sum(r[7] for r in rows)
    for row in sorted(rows, key=lambda x: -x[6]):
        coin, n, wr, exp, pf, dd, tot, lossabs, sd, net, cost = row
        P(f'{coin:<8}{n / tot_n * 100:>9.1f}%{tot / tot_sum * 100:>11.1f}%'
          f'{lossabs / tot_loss * 100:>11.1f}%')

    OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT.resolve()}')


if __name__ == '__main__':
    main()
