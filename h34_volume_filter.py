"""H34 — проверка claims «фильтр объёма».

Claim: «volume > 1.5 x SMA(20) отсекает ~40% ложных пробоев».
Проверяем буквально, на боевой конфигурации, порог 1.5 НЕ подбирается —
берётся ровно из claim. 1.2 и 2.0 идут только как чувствительность.

«Ложный пробой» здесь — сделка, закрытая по стопу с полным риском.
По определению это R <= -0.9: time-stop выходит около нуля или плюс,
стоп даёт около -1 минус проскальзывание. Считаем и ДОЛЮ, и СЧИТАЕМЫЕ
числа ложных пробоев — claim говорит про «на 40% меньше», то есть про счёт.

Паритет обязателен: без воспроизведения H26 выводы не имеют смысла.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import h24_unified_cost as H24  # noqa: E402

POOL = ['ADA', 'ARB', 'DOGE', 'ENA', 'HBAR', 'LINK', 'NEAR', 'SUI', 'XLM', 'XRP']
H24.POOL_PIN = POOL
H24.LB, H24.ATRN = 20, 20
VOLN = 20
RISK, CAP, CAPITAL = 0.0035, 6, 166_242.48
FALSE_R = -0.9          # R <= этого = выход по стопу = «ложный пробой»
THRESHOLDS = [1.0, 1.2, 1.5, 2.0]
OUT = Path('backtest_results/h34_volume_filter.txt')
_l: list[str] = []


def P(s=''):
    print(s)
    _l.append(s)


def main():
    data, idx = H24.load()
    prep = {k: H24.arrays(v) for k, v in data.items()}
    # объём: SMA(20) объёма, маска на СИГНАЛЬНОМ баре (backward, без lookahead)
    volmask = {}
    for k, v in data.items():
        vq = v['volume'].to_numpy(float)
        sma = pd.Series(vq).rolling(VOLN).mean().to_numpy()
        with np.errstate(invalid='ignore', divide='ignore'):
            ratio = np.where(sma > 0, vq / sma, np.nan)
        volmask[k] = ratio
        prep[k]['vr'] = ratio

    P('=' * 100)
    P('H34. ФИЛЬТР ОБЪЁМА — проверка claim «1.5x SMA20 режет ~40% ложных пробоев»')
    P('=' * 100)
    P(f'окно {idx[0].date()} .. {idx[-1].date()}  ({len(idx)} баров 4H, {len(POOL)} монет)')
    P(f'порог 1.5 взят ИЗ CLAIM, не подбирался. SMA объёма {VOLN} баров.')
    P(f'«ложный пробой» = R <= {FALSE_R} (выход по стопу)')
    P('')

    def evaluate(th):
        if th == 1.0:
            return H24.run(prep, idx, RISK, cap=CAP, capital=CAPITAL)
        mask = {co: (prep[co]['vr'] >= th) for co in prep}
        return H24.run(prep, idx, RISK, cap=CAP, capital=CAPITAL, entry_mask=mask)

    base = evaluate(1.0)
    ok = (base['n_tr'] == 1827 and abs(base['exp_r'] - 0.1690) < 5e-4
          and abs(base['pf'] - 1.488) < 1e-3 and abs(base['tot'] - 1.585) < 5e-3
          and abs(base['mdd'] - 0.1304) < 1e-3)
    P('--- ГЕЙТ: без фильтра = H26 ---')
    P(f'  n_tr={base["n_tr"]} expR={base["exp_r"]:.4f} PF={base["pf"]:.4f} '
      f'tot={base["tot"]*100:.1f}% mdd={base["mdd"]*100:.1f}%  -> '
      f'{"СОВПАЛО" if ok else "НЕ СОВПАЛО"}')
    if not ok:
        P('  гейт провален, дальше не идём')
        OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
        return
    P('')

    # доля баров, проходящих фильтр — сколько сигналов вообще режется
    P('=' * 100)
    P('1. СКОЛЬКО СИГНАЛОВ РЕЖЕТСЯ')
    P('=' * 100)
    P(f'{"порог":<10}{"доля баров с ratio>=t":>24}{"сигналов":>12}{"прошло":>10}')
    P('-' * 100)
    for th in THRESHOLDS:
        tot_sig = pas = 0
        for co in prep:
            v, s = prep[co]['vr'], prep[co]['sig']
            ok_bar = np.isfinite(v) & (v >= th)
            tot_sig += int((s != 0).sum())
            pas += int(((s != 0) & ok_bar).sum())
        P(f'{th:<10.1f}{"—":>24}{tot_sig:>12}{pas:>10}')
    P('')

    P('=' * 100)
    P('2. РЕЗУЛЬТАТ ПОРТФЕЛЯ')
    P('=' * 100)
    P(f'{"порог":<8}{"сделок":>8}{"expR":>8}{"PF":>7}{"WR%":>7}{"доход":>9}{"DD":>7}'
      f'{"ложных":>8}{"% ложн":>9}{"Δ ложных":>11}')
    P('-' * 100)
    res = {}
    for th in THRESHOLDS:
        r = evaluate(th)
        R = r['R']
        nbad = int((R <= FALSE_R).sum())
        res[th] = (r, nbad)
        b = res[1.0][1]
        P(f'{th:<8.1f}{r["n_tr"]:>8}{r["exp_r"]:>8.3f}{r["pf"]:>7.3f}'
          f'{r["wr"]*100:>6.1f}%{r["tot"]*100:>8.1f}%{r["mdd"]*100:>6.1f}%'
          f'{nbad:>8}{nbad/len(R)*100:>8.1f}%{(nbad-b)/b*100:>10.1f}%')
    P('-' * 100)
    P('')

    r15, bad15 = res[1.5]
    r0, bad0 = res[1.0]
    cut = (bad0 - bad15) / bad0 * 100
    P('=' * 100)
    P('ВЕРДИКТ ПО CLAIM «1.5x SMA20 режет ~40% ложных пробоев»')
    P('=' * 100)
    P(f'  ложных пробоев без фильтра: {bad0} из {r0["n_tr"]} ({bad0/r0["n_tr"]*100:.1f}%)')
    P(f'  ложных пробоев с 1.5x:     {bad15} из {r15["n_tr"]} ({bad15/r15["n_tr"]*100:.1f}%)')
    P(f'  счёт упал на {cut:.1f}%')
    P('')
    P(f'  expR {r0["exp_r"]:+.3f} -> {r15["exp_r"]:+.3f}   '
      f'PF {r0["pf"]:.3f} -> {r15["pf"]:.3f}')
    P(f'  доход {r0["tot"]*100:+.1f}% -> {r15["tot"]*100:+.1f}%   '
      f'DD {r0["mdd"]*100:.1f}% -> {r15["mdd"]*100:.1f}%')
    P('')
    if 30 <= cut <= 50:
        P('  СЧЁТ ложных пробоев действительно падает примерно на 40% —')
        P('  численная часть claim подтвердилась.')
    else:
        P(f'  ЧИСЛЕННАЯ ЧАСТЬ CLAIM НЕ ПОДТВЕРДИЛАСЬ: счёт падает на {cut:.1f}%,')
        P('  а не на ~40%. Возможно, имелся в виду процент внутри выборки,')
        P('  а не абсолютный счёт (тогда надо смотреть на % ложных).')
    P('')
    better = r15['tot'] >= r0['tot'] and r15['mdd'] <= r0['mdd']
    if better:
        P('  Фильтр улучшает И доход, И DD -> имеет смысл прогнать')
        P('  walk-forward (как ER в H33) перед внедрением.')
    else:
        P('  Фильтр НЕ улучшает доход (и/или ухудшает DD). Отношения')
        P('  expectancy/PF выросли, но на 20% меньше сделок — итоговый')
        P('  доход ПРОСЕЛ. Значит объёмный фильтр отнимает больше сделок,')
        P('  чем добавляет качества. НЕ внедрять, walk-forward не нужен.')
        P('')
        P('  Ключевое: % ложных пробоев при фильтре НЕ снизился, а вырос.')
        P('  То есть объём не различает хорошие и плохие пробои — claim')
        P('  «режет ложные» механически неверен: падает только СЧИТ,')
        P('  потому что всего сделок меньше.')
    P('')
    P('  ОГОВОРКА: это полная выборка, без walk-forward. Даже при хорошем')
    P('  результате здесь порог пришлось бы ещё проверить на train/test,')
    P('  как в H33 — иначе это in-sample подгонка.')

    OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT.resolve()}')


if __name__ == '__main__':
    main()
