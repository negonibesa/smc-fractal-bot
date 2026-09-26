"""H30 — портфельная проверка пулов с новыми кандидатами + MC + walk-forward.

Два вида окон, потому что RENDER торгуется с 2024-07 и урезает любое
пересечение:
  1) ОБЩЕЕ окно — пересечение pool10 + всех кандидатов. Все пулы считаются
     на одном периоде, сравнение apples-to-apples.
  2) СВОЁ окно — пересечение только своих членов. Это то, что получилось бы
     в бою, если монету реально добавить.

cap=6 — связывающее ограничение, поэтому добавление монет может не дать
прироста (каннибализация слотов + корреляция). Это и проверяем.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import h24_unified_cost as H24  # noqa: E402

COST_RT = 0.0014
POOL10 = ['ADA', 'ARB', 'DOGE', 'ENA', 'HBAR', 'LINK', 'NEAR', 'SUI', 'XLM', 'XRP']
PAYING = ['AVAX', 'RENDER', 'TRX', 'UNI']          # netR > 0.05 в H29

POOLS = [
    ('pool10 (текущий)', POOL10),
    ('+TRX', POOL10 + ['TRX']),
    ('+UNI', POOL10 + ['UNI']),
    ('+RENDER', POOL10 + ['RENDER']),
    ('+RENDER+UNI', POOL10 + ['RENDER', 'UNI']),
    ('+TRX+RENDER', POOL10 + ['TRX', 'RENDER']),
    ('+TRX+RENDER+UNI', POOL10 + ['TRX', 'RENDER', 'UNI']),
    ('+TRX+UNI', POOL10 + ['TRX', 'UNI']),
    ('+TRX+UNI+AVAX', POOL10 + ['TRX', 'UNI', 'AVAX']),
    ('+TRX+UNI+RENDER', POOL10 + ['TRX', 'UNI', 'RENDER']),
    ('+все 4 (14 монет)', POOL10 + PAYING),
]

OUT = Path('backtest_results/h30_pool_expand.txt')
_l: list[str] = []


def P(s=''):
    print(s)
    _l.append(s)


def load_coin(base):
    f = H24.DATA / f"{base}_4h_2020-01-01_top20.csv"
    if not f.exists():
        f = next(H24.DATA.glob(f"{base}_4h*.csv"), None)
    return pd.read_csv(f, index_col=0, parse_dates=True) if f else None


_cache = {}


def coin(base):
    if base not in _cache:
        _cache[base] = load_coin(base)
    return _cache[base]


def window_for(pool):
    idx = None
    for b in pool:
        d = coin(b)
        if d is None:
            return None
        idx = d.index if idx is None else idx.intersection(d.index)
    idx = idx.sort_values()
    return idx[(idx >= idx.min()) & (idx <= idx.max())]


def prep_for(pool, idx):
    return {b: H24.arrays(coin(b).reindex(idx).ffill()) for b in pool if coin(b) is not None}


def mc_stats(R, risk):
    """H24.mc отдаёт кортеж (tots, mdds) — приводим к словарю."""
    tots, mdds = H24.mc(R, risk)
    return dict(tot_p5=float(np.percentile(tots, 5)), tot_med=float(np.median(tots)),
                tot_p95=float(np.percentile(tots, 95)),
                p5_dd=float(np.percentile(mdds, 5)), med_dd=float(np.median(mdds)),
                p95_dd=float(np.percentile(mdds, 95)),
                p_dd20=float((mdds > 0.20).mean()), p_dd30=float((mdds > 0.30).mean()))


def main():
    risk = 0.0035
    all_coins = sorted(set(POOL10) | set(PAYING))
    common = window_for(all_coins)
    own = {name: window_for(p) for name, p in POOLS}

    P('=' * 112)
    P('H30. ПОРТФЕЛЬНОЕ РАСШИРЕНИЕ ПУЛА (Donchian 20, cap=6, риск 0.35%, издержки H24)')
    P('=' * 112)
    P(f'ОБЩЕЕ окно (pool10 + все кандидаты): {common[0].date()} .. {common[-1].date()}  '
      f'({len(common)} баров 4H)')
    P('на этом окне сравниваются ВСЕ пулы ниже — иначе RENDER (с 2024-07)')
    P('урезал бы выборку и пулы сравнивались бы на разных периодах')
    P('')

    P('=' * 112)
    P('1. ПОРТФЕЛИ НА ОБЩЕМ ОКНЕ (честное сравнение, одинаковый период)')
    P('=' * 112)
    P(f'{"пул":<22}{"монет":>7}{"сделок":>8}{"expR":>8}{"PF":>7}{"WR%":>7}'
      f'{"доходн":>9}{"DD":>8}{"экспоз":>9}{"мес/мес":>9}')
    P('-' * 112)
    res = {}
    for name, pool in POOLS:
        idx = common
        r = H24.run(prep_for(pool, idx), idx, risk)
        res[name] = r
        months = (idx[-1] - idx[0]).days / 30.44
        P(f'{name:<22}{len(pool):>7}{r["n_tr"]:>8}{r["exp_r"]:>8.3f}{r["pf"]:>7.3f}'
          f'{r["wr"]*100:>6.1f}%{r["tot"]*100:>8.1f}%{r["mdd"]*100:>7.1f}%'
          f'{r["exposure"]*100:>8.1f}%{r["tot"]*100/months:>8.2f}%')
    P('-' * 112)
    P('')

    P('=' * 112)
    P('2. MONTE CARLO 3000 (block=50, seed=20260926) — общий риск 0.35%')
    P('=' * 112)
    P(f'{"пул":<22}{"сделок":>8}{"dd p5":>9}{"dd med":>9}{"dd p95":>9}'
      f'{"P(>20%)":>9}{"P(>30%)":>9}')
    P('-' * 112)
    mcres = {}
    for name, pool in POOLS:
        idx = common
        m = mc_stats(res[name]['R'], risk)
        mcres[name] = m
        P(f'{name:<22}{res[name]["n_tr"]:>8}{m["p5_dd"]*100:>8.1f}%{m["med_dd"]*100:>8.1f}%'
          f'{m["p95_dd"]*100:>8.1f}%{m["p_dd20"]*100:>8.1f}%{m["p_dd30"]*100:>8.1f}%')
    P('-' * 112)
    P('')

    P('=' * 112)
    P('3. ПУЛЫ НА СВОЁМ ОКНЕ (что получилось бы в бою после реального добавления)')
    P('=' * 112)
    P(f'{"пул":<22}{"окно":<26}{"баров":>7}{"доходн":>9}{"DD":>8}{"dd p5":>8}{"dd p95":>8}')
    P('-' * 112)
    for name, pool in POOLS:
        idx = own[name]
        r = H24.run(prep_for(pool, idx), idx, risk)
        m = mc_stats(r['R'], risk)
        P(f'{name:<22}{str(idx[0].date())+".."+str(idx[-1].date()):<26}{len(idx):>7}'
          f'{r["tot"]*100:>8.1f}%{r["mdd"]*100:>7.1f}%{m["p5_dd"]*100:>7.1f}%'
          f'{m["p95_dd"]*100:>7.1f}%')
    P('-' * 112)
    P('')

    P('=' * 112)
    P('4. ДЕЛЬТА К ТЕКУЩЕМУ ПУЛУ (общее окно)')
    P('=' * 112)
    b = res['pool10 (текущий)']
    P(f'{"пул":<22}{"d сделок":>10}{"d expR":>9}{"d PF":>8}{"d доходн":>11}{"d DD":>9}')
    P('-' * 112)
    for name, _ in POOLS[1:]:
        r = res[name]
        P(f'{name:<22}{r["n_tr"]-b["n_tr"]:>+10}{r["exp_r"]-b["exp_r"]:>+9.3f}'
          f'{r["pf"]-b["pf"]:>+8.3f}{(r["tot"]-b["tot"])*100:>+10.1f}%'
          f'{(r["mdd"]-b["mdd"])*100:>+8.1f}%')
    P('-' * 112)

    OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT.resolve()}')


if __name__ == '__main__':
    main()



