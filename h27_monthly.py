"""H27 — помесячная разбивка портфеля pool10 (Donchian 20).

Использует ровно ту же логику, что H24/H26 (equity-sizing, cap 6, издержки
0.14% RT, стоп ДО обновления экстремума, time-stop 30 баров), но раскладывает
результат по календарным месяцам: сделки, доходность, expectancy.

Метрики не пересчитываются иначе, чем в H26 — это та же симуляция, только
группировка по месяцам. Аддитивное поле R_bar/R_sym добавлено в h24.run()
и на существующие числа не влияет.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import h24_unified_cost as H24  # noqa: E402

POOL10_EXCL = {'XMR', 'LTC', 'BTC', 'SOL', 'TON', 'APT', 'ETH'}
OUT = Path('backtest_results/h27_monthly_pool10.txt')
_l: list[str] = []


def P(s=''):
    print(s)
    _l.append(s)


def main():
    H24.EXCLUDE_14 = set(POOL10_EXCL)
    data, idx = H24.load()
    prep = {k: H24.arrays(v) for k, v in data.items()}
    r = H24.run(prep, idx, 0.0035)

    eq, dd, R = r['eq'], r['dd'], r['R']
    bars = r['R_bar']
    syms = r['R_sym']

    # сделки группируем по месяцу ВЫХОДА, доходность — по изменению equity
    # bars — индексы баров выхода, R индексируется по порядку сделок
    bars_per_month = defaultdict(int)
    trades_per_month = defaultdict(int)
    r_sum = defaultdict(float)
    for k, j in enumerate(bars):
        month = idx[j].strftime('%Y-%m')
        trades_per_month[month] += 1
        r_sum[month] += R[k]
    for d in idx:
        bars_per_month[d.strftime('%Y-%m')] += 1

    months = sorted(trades_per_month)
    n_months = len(months)
    first, last = idx[0], idx[-1]
    span_months = (last.year - first.year) * 12 + (last.month - first.month) + 1

    P('=' * 96)
    P('H27. ПОМЕСЯЧНАЯ РАЗБИВКА ПОРТФЕЛЯ pool10 (Donchian 20, risk 0.35%, cap 6)')
    P('=' * 96)
    P('')
    P(f'период данных      {first.date()} .. {last.date()}  '
      f'({span_months} мес., {len(idx)} баров 4H)')
    P(f'монет в пуле       {len(data)}: {" ".join(sorted(data))}')
    P('')

    P('--- ИТОГО ЗА ПЕРИОД ---')
    P(f'  сделок           {r["n_tr"]:>8}')
    P(f'  сделок в месяц   {r["n_tr"] / span_months:>8.1f}  (в среднем)')
    P(f'  сделок в месяц   {np.median([trades_per_month[m] for m in months]):>8.1f}  (медиана)')
    P(f'  итог             {r["tot"] * 100:>7.1f} %')
    P(f'  в месяц (CAGR)   {((1 + r["tot"]) ** (12 / span_months) - 1) * 100:>7.2f} %')
    P(f'  expectancy       {r["exp_r"]:>8.4f} R')
    P(f'  PF               {r["pf"]:>8.3f}')
    P(f'  winrate          {r["wr"] * 100:>7.2f} %')
    P(f'  макс. DD         {r["mdd"] * 100:>7.2f} %')
    P(f'  exposure         {r["exposure"] * 100:>7.2f} %')
    P('')

    # позиции баров по месяцам — get_loc вместо index(), idx это DatetimeIndex
    month_slices = {}
    for m in months:
        pos = [i for i, d in enumerate(idx) if d.strftime('%Y-%m') == m]
        month_slices[m] = (pos[0], pos[-1])

    P('--- ПОМЕСЯЧНО ---')
    P(f'{"месяц":<10}{"баров":>7}{"сделок":>8}{"R сумма":>10}'
      f'{"equity %":>11}{"DD %":>8}')
    P('-' * 96)
    for m in months:
        i0, i1 = month_slices[m]
        e = eq[i0:i1 + 1]
        # доходность месяца и DD внутри месяца: пик от начала месяца,
        # иначе закрывающий месяц показывает 100% из-за пика прошлого месяца
        peak = np.maximum.accumulate(e)
        mdd = ((peak - e) / peak).max()
        P(f'{m:<10}{bars_per_month[m]:>7}{trades_per_month[m]:>8}'
          f'{r_sum[m]:>10.2f}{((e[-1] / e[0]) - 1) * 100:>10.1f}%{mdd * 100:>7.1f}%')
    P('-' * 96)
    P(f'{"ВСЕГО":<10}{len(idx):>7}{r["n_tr"]:>8}{R.sum():>10.2f}'
      f'{r["tot"] * 100:>10.1f}%{r["mdd"] * 100:>7.1f}%')
    P('')

    # доходность в % корректно не складывается (это equity, а не сумма PnL),
    # поэтому показываем распределение по годам отдельно
    P('--- ПОГОДНО (для сверки: сумма месячных доходностей не равна итогу) ---')
    P(f'{"год":<8}{"сделок":>8}{"сделок/мес":>13}{"доходность":>12}{"DD %":>8}')
    P('-' * 96)
    for yr in sorted({d.year for d in idx}):
        yt = sum(trades_per_month[m] for m in months if m.startswith(str(yr)))
        ym = len([m for m in months if m.startswith(str(yr))])
        e = eq[np.array([d.year == yr for d in idx])]
        peak = np.maximum.accumulate(e)
        P(f'{yr:<8}{yt:>8}{yt / max(ym, 1):>13.1f}'
          f'{((e[-1] / e[0]) - 1) * 100:>11.1f}%{((peak - e) / peak).max() * 100:>7.1f}%')
    P('')

    P('--- СДЕЛОК ПО МОНЕТАМ (всего за период) ---')
    per_sym = defaultdict(int)
    per_sym_r = defaultdict(float)
    for s, rv in zip(syms, R):
        per_sym[s] += 1
        per_sym_r[s] += rv
    P(f'{"монета":<12}{"сделок":>8}{"сделок/мес":>13}{"R сумма":>10}')
    P('-' * 96)
    for s in sorted(per_sym, key=lambda x: -per_sym[x]):
        P(f'{s:<12}{per_sym[s]:>8}{per_sym[s] / span_months:>13.1f}{per_sym_r[s]:>10.2f}')
    P('')

    P('--- РАСПРЕДЕЛЕНИЕ ПО ЧИСЛУ СДЕЛОК В МЕСЯЦ ---')
    counts = np.array([trades_per_month[m] for m in months])
    P(f'  минимум         {counts.min():>6}')
    P(f'  25%             {np.percentile(counts, 25):>6.0f}')
    P(f'  медиана         {np.median(counts):>6.0f}')
    P(f'  75%             {np.percentile(counts, 75):>6.0f}')
    P(f'  максимум        {counts.max():>6}')
    P(f'  среднее         {counts.mean():>6.1f}')
    P(f'  месяцев без сделок  {(counts == 0).sum():>3} из {len(months)}')
    P('')
    P('ВНИМАНИЕ: все метрики in-sample на тех же данных, что использовались для')
    P('отбора пула и настройки параметров (H17-H26). Это не out-of-sample')
    P('прогноз, а описание уже случившегося. Funding, проскальзывание на')
    P('внутрибазовых срабатываниях стопа и стресс-периоды не моделируются.')

    OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT.resolve()}')


if __name__ == '__main__':
    main()
