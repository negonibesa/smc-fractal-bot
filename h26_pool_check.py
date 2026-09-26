"""H26 — портфельная проверка пула после H25 (удаление аутсайдеров).

Прогоняет ровно ту же логику, что H24 (equity-sizing, cap 6, издержки 0.14%,
стоп до обновления экстремума), но на разных пулах. Цель: убедиться, что
H25-фильтр не сломал портфельные метрики, на которых выбирались risk 0.35%
и halt 20%.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import h24_unified_cost as H24  # noqa: E402

BASE_EXCL = {'XMR', 'LTC', 'BTC'}
POOLS = [
    ('pool14 (H24, база)', BASE_EXCL),
    ('pool10 (H25: -SOL -TON -APT -ETH)', BASE_EXCL | {'SOL', 'TON', 'APT', 'ETH'}),
    ('pool9  (H25: -SOL -TON -APT -ETH -LINK)',
     BASE_EXCL | {'SOL', 'TON', 'APT', 'ETH', 'LINK'}),
    ('pool7  (топ-7 по netR)',
     BASE_EXCL | {'SOL', 'TON', 'APT', 'ETH', 'LINK', 'HBAR', 'DOGE'}),
]

OUT = Path('backtest_results/h26_pool_check.txt')
_l: list[str] = []


def P(s=''):
    print(s)
    _l.append(s)


def main():
    P('=' * 108)
    P('H26. ПОРТФЕЛЬНАЯ ПРОВЕРКА ПУЛА  (equity-sizing, cap 6, 0.14% RT, risk 0.35%)')
    P('=' * 108)
    P('')

    results = {}
    for label, excl in POOLS:
        H24.EXCLUDE_14 = set(excl)
        data, idx = H24.load()
        prep = {k: H24.arrays(v) for k, v in data.items()}
        r = H24.run(prep, idx, 0.0035)
        results[label] = (r, sorted(data))
        P(f'--- {label}  ({len(data)} монет) ---')
        P(f'  сделок        {r["n_tr"]:>8}')
        P(f'  expectancy    {r["exp_r"]:>8.4f} R')
        P(f'  PF            {r["pf"]:>8.3f}')
        P(f'  winrate       {r["wr"] * 100:>7.2f} %')
        P(f'  итог          {r["tot"] * 100:>7.1f} %')
        P(f'  макс. DD      {r["mdd"] * 100:>7.2f} %')
        P(f'  exposure      {r["exposure"] * 100:>7.2f} %')
        P('')

    P('=' * 108)
    P('МОНТЕ-КАРЛО (3000 итераций, блочная перетасовка, тот же seed)')
    P('=' * 108)
    P(f'{"пул":<44}{"expR":>8}{"ret p5":>10}{"med":>9}{"p95":>10}'
      f'{"DD p50":>8}{"DD p95":>8}{"P>30%":>8}{"Calmar":>8}')
    P('-' * 108)
    for label, (r, coins) in results.items():
        tots, mdds = H24.mc(r['R'], 0.0035)
        calmar = np.median(tots) / np.percentile(mdds, 95) if np.percentile(mdds, 95) else np.nan
        P(f'{label:<44}{r["exp_r"]:>8.4f}{np.percentile(tots, 5) * 100:>9.1f}%'
          f'{np.median(tots) * 100:>8.1f}%{np.percentile(tots, 95) * 100:>9.1f}%'
          f'{np.median(mdds) * 100:>7.1f}%{np.percentile(mdds, 95) * 100:>7.1f}%'
          f'{(mdds > 0.30).mean() * 100:>7.1f}%{calmar:>8.2f}')
    P('')

    P('--- годовая разбивка (pool10) ---')
    H24.EXCLUDE_14 = POOLS[1][1]
    data, idx = H24.load()
    prep = {k: H24.arrays(v) for k, v in data.items()}
    r = H24.run(prep, idx, 0.0035)
    eq, dd = r['eq'], r['dd']
    for yr in sorted({d.year for d in idx}):
        m = np.array([d.year == yr for d in idx])
        e = eq[m]
        # DD внутри года: пик считаем от начала года, иначе месячная
        # просадка отражает пик, достигнутый в прошлом году, и даёт 100%.
        base = e[0]
        peak = np.maximum.accumulate(e)
        ydd = ((peak - e) / peak).max()
        P(f'  {yr}: возврат {((e[-1] / base) - 1) * 100:>7.1f}%  '
          f'DD {ydd * 100:>5.1f}%  баров {m.sum()}')
    P('')
    P('состав pool10: ' + ' '.join(results[POOLS[1][0]][1]))

    OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT.resolve()}')


if __name__ == '__main__':
    main()
