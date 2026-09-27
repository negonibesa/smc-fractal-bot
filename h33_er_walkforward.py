"""H33 — walk-forward проверка ER-фильтра. Гипотеза «режим объясняет P&L».

H32 нашёл: пробои Donchian зарабатывают только когда рынок реально трендовый
(Kaufman ER20 выше медианы): expR +0.319 против -0.097, p<0.0001, эффект
одинаков в обеих половинах по волатильности.

НО порог там был медианой по ВСЕМУ периоду. Это in-sample, ровно тот ход,
который дал H6. Здесь порог выбирается на train и замораживается на test.

Варианты порога заданы ЗАРАНЕЕ, до просмотра OOS:
  A  — медиана ER по train, своя для каждой монеты
  B  — медиана ER по train, общая на весь пул
  A40/A60 — то же, но 40-й и 60-й процентиль: проверка на лезвие
Ничего не выбирается по результату OOS: все варианты печатаются целиком.

Параметры стратегии зафиксированы на боевых (lb20/ATR20/trail2.0/hold30) —
подбирается ТОЛЬКО порог фильтра, и то на train.
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

ER_N = 20
TRAIN_MONTHS, TEST_MONTHS, N_WINDOWS = 6, 4, 6
RISK, CAP, CAPITAL = 0.0035, 6, 166_242.48
OUT = Path('backtest_results/h33_er_walkforward.txt')
_l: list[str] = []


def P(s=''):
    print(s)
    _l.append(s)


def _key(ts):
    return ts.year * 12 + ts.month


def month_span(idx, start_key, n_months):
    s, e = start_key, start_key + n_months
    lo = next((i for i, t in enumerate(idx) if _key(t) >= s), len(idx))
    hi = next((i for i, t in enumerate(idx) if _key(t) >= e), len(idx))
    return lo, hi


def er_series(c, n=ER_N):
    """Kaufman Efficiency Ratio. Строго backward: значение на баре k видит
    только бары <= k, поэтому lookahead невозможен по построению."""
    net = np.abs(c - np.roll(c, n))
    net[:n] = np.nan
    path = pd.Series(np.abs(np.diff(c, prepend=c[0]))).rolling(n).sum().to_numpy()
    return net / np.where(path > 0, path, np.nan)


def win_metrics(res, lo, hi):
    """Метрики окна из прогона по idx[:hi] с маской входа только в [lo,hi)."""
    eq = res['eq'][lo:hi]
    R = res['R'][(res['R_bar'] >= lo) & (res['R_bar'] < hi)]
    if len(eq) == 0:
        return dict(tot=np.nan, mdd=np.nan, n_tr=0, exp_r=np.nan, pf=np.nan,
                    wr=np.nan, R=np.array([]))
    peak = np.maximum.accumulate(eq)
    w, l = R[R > 0].sum(), -R[R < 0].sum()
    return dict(tot=float(eq[-1] / eq[0] - 1), mdd=float((peak - eq).max() / peak.max()),
                n_tr=len(R), exp_r=float(R.mean()) if len(R) else np.nan,
                pf=float(w / l) if l > 0 else np.nan,
                wr=float((R > 0).mean()) if len(R) else np.nan, R=R)


def main():
    data, idx = H24.load()
    prep = {k: H24.arrays(v) for k, v in data.items()}
    er = {k: er_series(v['c']) for k, v in prep.items()}

    P('=' * 104)
    P('H33. WALK-FORWARD ПРОВЕРКА ER-ФИЛЬТРА (порог на train, заморожен на test)')
    P('=' * 104)
    P(f'окно {idx[0].date()} .. {idx[-1].date()}  ({len(idx)} баров 4H, {len(POOL)} монет)')
    P(f'стратегия зафиксирована: lb{H24.LB}/ATR{H24.ATRN}/trail2.0/hold30, '
      f'risk {RISK*100:.2f}%, cap {CAP}')
    P(f'фильтр: ER{ER_N} >= порог (порог выбирается на train и не пересчитывается в окне)')
    P('')

    # ─── гейт паритета: baseline полного периода = H26 ──────────────
    full = H24.run(prep, idx, RISK, cap=CAP, capital=CAPITAL)
    ok = (full['n_tr'] == 1827 and abs(full['exp_r'] - 0.1690) < 5e-4
          and abs(full['pf'] - 1.488) < 1e-3 and abs(full['tot'] - 1.585) < 5e-3
          and abs(full['mdd'] - 0.1304) < 1e-3)
    P('--- ГЕЙТ: baseline воспроизводит H26 ---')
    P(f'  n_tr={full["n_tr"]} expR={full["exp_r"]:.4f} PF={full["pf"]:.4f} '
      f'tot={full["tot"]*100:.1f}% mdd={full["mdd"]*100:.1f}%  -> '
      f'{"СОВПАЛО" if ok else "НЕ СОВПАЛО"}')
    if not ok:
        P('  гейт провален, дальше не идём')
        OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
        return
    P('')

    first = _key(idx[0]) + TRAIN_MONTHS
    windows = []
    for w in range(N_WINDOWS):
        lo, hi = month_span(idx, first + w * TEST_MONTHS, TEST_MONTHS)
        if hi - lo < 50:
            break
        windows.append((w + 1, lo, hi))
    P(f'окон {len(windows)}, train расширяется с {TRAIN_MONTHS} до '
      f'{TRAIN_MONTHS + (len(windows)-1)*TEST_MONTHS} мес.')
    P('')

    VAR = ['A', 'B', 'A40', 'A60']

    def thresholds(variant, tr_end):
        """Порог(и) по train-барам [0, tr_end)."""
        if variant in ('A', 'A40', 'A60'):
            q = {'A': .5, 'A40': .4, 'A60': .6}[variant]
            return {co: np.nanpercentile(er[co][:tr_end], q * 100) for co in er}
        allv = np.concatenate([er[co][:tr_end] for co in sorted(er)])
        t = np.nanmedian(allv)
        return {co: t for co in er}

    rows = []
    P('=' * 104)
    P('1. WALK-FORWARD: baseline против вариантов фильтра')
    P('=' * 104)
    for w, lo, hi in windows:
        tr_end = lo
        P(f'окно {w}  test {idx[lo].date()}..{idx[hi-1].date()}  '
          f'train до {idx[tr_end-1].date()}')
        bl = win_metrics(H24.run(prep, idx[:hi], RISK, cap=CAP, capital=CAPITAL), lo, hi)
        P(f'   {"baseline":<10}{bl["n_tr"]:>7}{bl["exp_r"]:>8.3f}{bl["pf"]:>7.3f}'
          f'{bl["wr"]*100:>6.1f}%{bl["tot"]*100:>8.1f}%{bl["mdd"]*100:>6.1f}%')
        row = dict(w=w, bl=bl)
        for var in VAR:
            th = thresholds(var, tr_end)
            # маска: вход разрешён только внутри окна и только при ER >= порога
            mask = {co: (er[co] >= th[co]) for co in er}
            r = win_metrics(H24.run(prep, idx[:hi], RISK, cap=CAP,
                                    capital=CAPITAL, entry_mask=mask), lo, hi)
            ths = ' / '.join(f'{th[c]:.3f}' for c in sorted(th)[:3])
            P(f'   {var:<10}{r["n_tr"]:>7}{r["exp_r"]:>8.3f}{r["pf"]:>7.3f}'
              f'{r["wr"]*100:>6.1f}%{r["tot"]*100:>8.1f}%{r["mdd"]*100:>6.1f}%'
              f'   пороги {ths} ...')
            row[var] = r
        rows.append(row)
        P('')

    P('=' * 104)
    P('2. СВОДКА ПО ОКНАМ (OOS, 24 месяца)')
    P('=' * 104)
    P(f'{"вариант":<12}{"сделок":>7}{"expR":>8}{"PF":>7}{"WR":>7}'
      f'{"сумма дох":>11}{"худшDD":>8}{"окон PF>=1":>12}{"Δдоход":>9}{"ΔDD":>8}')
    P('-' * 104)

    def compound(tots):
        acc = 1.0
        for t in tots:
            acc *= (1 + t)
        return acc - 1

    bl_tot = compound([r['bl']['tot'] for r in rows])
    bl_mdd = max(r['bl']['mdd'] for r in rows)
    bl_R = np.concatenate([r['bl']['R'] for r in rows])
    bl_nok = sum(1 for r in rows if r['bl']['pf'] >= 1)
    P(f'{"baseline":<12}{len(bl_R):>7}{bl_R.mean():>8.3f}'
      f'{bl_R[bl_R>0].sum()/-bl_R[bl_R<0].sum():>7.3f}{(bl_R>0).mean()*100:>6.1f}%'
      f'{bl_tot*100:>10.1f}%{bl_mdd*100:>7.1f}%'
      f'{bl_nok:>8}/{len(rows):<3}'
      f'{"—":>9}{"—":>8}')
    summary = dict(bl=dict(R=bl_R, tot=bl_tot, mdd=bl_mdd, n=len(bl_R), nok=bl_nok))
    for var in VAR:
        Rs = np.concatenate([r[var]['R'] for r in rows])
        tot = compound([r[var]['tot'] for r in rows])
        mdd = max(r[var]['mdd'] for r in rows)
        nok = sum(1 for r in rows if r[var]['pf'] >= 1)
        P(f'{var:<12}{len(Rs):>7}{Rs.mean():>8.3f}'
          f'{Rs[Rs>0].sum()/-Rs[Rs<0].sum():>7.3f}{(Rs>0).mean()*100:>6.1f}%'
          f'{tot*100:>10.1f}%{mdd*100:>7.1f}%{nok:>8}/{len(rows):<3}'
          f'{(tot-bl_tot)*100:>8.1f}%{(mdd-bl_mdd)*100:>7.1f}%')
        summary[var] = dict(R=Rs, tot=tot, mdd=mdd, n=len(Rs), nok=nok)
    P('-' * 104)
    P('')

    P('=' * 104)
    P('3. МОНТЕ-КАРЛО (блоковый бутстреп трейдов OOS, 3000 итераций)')
    P('=' * 104)
    P(f'{"вариант":<12}{"med доход":>12}{"p5":>9}{"p95":>9}{"DD p95":>9}{"P(DD>20%)":>11}')
    P('-' * 104)
    for nm in ['bl'] + VAR:
        Rs = summary[nm]['R']
        t, d = H24.mc(Rs, RISK)
        P(f'{nm:<12}{np.median(t)*100:>11.1f}%{np.percentile(t,5)*100:>8.1f}%'
          f'{np.percentile(t,95)*100:>8.1f}%{np.percentile(d,95)*100:>8.1f}%'
          f'{(d>0.20).mean()*100:>10.1f}%')
    P('-' * 104)
    P('')

    P('=' * 104)
    P('ВЕРДИКТ')
    P('=' * 104)
    best = max(VAR, key=lambda v: summary[v]['tot'])
    b = summary[best]
    d = (b['tot'] - bl_tot) * 100
    dd = (b['mdd'] - bl_mdd) * 100
    P(f'  лучший вариант по OOS-доходу: {best}   {b["tot"]*100:+.1f}% против '
      f'baseline {bl_tot*100:+.1f}%   Δ {d:+.1f} п.п.')
    P(f'  DD: {b["mdd"]*100:.1f}% против {bl_mdd*100:.1f}%   Δ {dd:+.1f} п.п.')
    P(f'  окон с PF >= 1: {b["nok"]}/{len(rows)} против baseline {bl_nok}/{len(rows)}')
    P('')
    wins = [v for v in VAR
            if summary[v]['tot'] > bl_tot and summary[v]['mdd'] < bl_mdd]
    P(f'  варианты, лучшие сразу по доходу И по DD: {wins if wins else "нет"}')
    if d > 0:
        P('  -> фильтр даёт прирост OOS. Это кандидат в live, но требует')
        P('     подтверждения на будущих данных (у нас их нет) и решения,')
        P('     сколько live-истории нужно на разгон порога.')
    else:
        P('  -> прироста на OOS НЕТ. H32-эффект не переносится: это была')
        P('     подгонка по всей выборке, ровно H6. Фильтр НЕ внедрять.')
    P('')
    P('  ОГОВОРКИ:')
    P('   1. Это тот же период 2024-04..2026-09. Walk-forward честен по')
    P('      параметрам, но НЕ по выбору самого вопроса «а не попробовать ли')
    P('      ER?» — этот вопрос задан на всей выборке. Реально независимой')
    P('      проверки у нас не было и нет.')
    P('   2. Отбор вариантов A/B/A40/A60 сделан ДО просмотра OOS, но это')
    P('      четыре варианта одного фильтра; лучший из них постфактум.')
    P('   3. ER20 требует 20 баров прогрева — на живом боте это мгновенно,')
    P('      проблемы нет.')

    OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT.resolve()}')


if __name__ == '__main__':
    main()
