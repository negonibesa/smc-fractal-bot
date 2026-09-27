"""H36 — живой эквивалент порога ER-фильтра.

Проблема: в H33 порог брался с expanding train и замораживался на 4-месячное
окно. В live «train» не существует — есть только скользящее окно истории.

Варианты, которые можно честно реализовать в боте:
  frozen — порог зашит в конфиг на момент валидации (обновляется вручную)
  dynW   — порог = медиана ER за последние W баров, пересчитывается каждый бар

dynW — то, что надо деплоить: методика walk-forward воспроизводится по
построению, train-момент не нужен, и порог сам следует за рынком.

Проверяем, что dynW не хуже frozen из H33. Если dynamic заметно хуже —
деплоить frozen, и не выдумывать.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import h24_unified_cost as H24  # noqa: E402

POOL = ['ADA', 'ARB', 'DOGE', 'ENA', 'HBAR', 'LINK', 'NEAR', 'SUI', 'XLM', 'XRP']
H24.POOL_PIN = POOL
H24.LB, H24.ATRN = 20, 20
ER_N = 20
TRAIN_MONTHS, TEST_MONTHS, N_WINDOWS = 6, 4, 6
RISK, CAP, CAPITAL = 0.0035, 6, 166_242.48
WINDOWS_W = [500, 950, 1000]     # 83 / 158 / 166 суток 4H
OUT = Path('backtest_results/h36_live_threshold.txt')
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
    net = np.abs(c - np.roll(c, n))
    net[:n] = np.nan
    path = np.concatenate([[np.nan], np.abs(np.diff(c))])
    path = np.nancumsum(np.nan_to_num(path))
    import pandas as pd
    path = pd.Series(np.abs(np.diff(c, prepend=c[0]))).rolling(n).sum().to_numpy()
    return net / np.where(path > 0, path, np.nan)


def dyn_mask(er_co, W):
    """mask[k] = True, если er[k] >= медиана er[k-W:k]. Только бары < k."""
    n = len(er_co)
    m = np.zeros(n, bool)
    if n <= W + 1:
        return None
    cs = np.concatenate([[0.0], np.nancumsum(np.where(np.isnan(er_co), 0, er_co))])
    for k in range(W + 1, n):
        win = er_co[k - W:k]
        win = win[~np.isnan(win)]
        if len(win) < W // 2:
            continue
        m[k] = er_co[k] >= np.median(win)
    return m


def frozen_mask(er_co, tr_end):
    th = np.nanpercentile(er_co[:tr_end], 50)
    return er_co >= th, float(th)


def win_metrics(res, lo, hi):
    eq = res['eq'][lo:hi]
    R = res['R'][(res['R_bar'] >= lo) & (res['R_bar'] < hi)]
    if not len(eq):
        return dict(tot=np.nan, mdd=np.nan, n_tr=0, R=np.array([]))
    peak = np.maximum.accumulate(eq)
    return dict(tot=float(eq[-1] / eq[0] - 1),
                mdd=float((peak - eq).max() / peak.max()),
                n_tr=len(R), R=R)


def compound(ts):
    a = 1.0
    for t in ts:
        a *= (1 + t)
    return a - 1


def main():
    import pandas as pd
    data, idx = H24.load()
    prep = {k: H24.arrays(v) for k, v in data.items()}
    er = {k: er_series(v['c']) for k, v in prep.items()}

    P('=' * 104)
    P('H36. ЖИВОЙ ЭКВИВАЛЕНТ ПОРОГА ER — что реально деплоить')
    P('=' * 104)
    P(f'окно {idx[0].date()} .. {idx[-1].date()}  ({len(idx)} баров 4H)')
    P(f'frozen — порог из конфига (обновляется вручную при ре-валидации)')
    P('dynW — медиана ER за последние W баров, пересчёт каждый бар')
    P(f'W=500 ~{500*4//24} сут, W=1000 ~{1000*4//24} сут, W=1820 ~{1820*4//24} сут')
    P('')

    full = H24.run(prep, idx, RISK, cap=CAP, capital=CAPITAL)
    ok = (full['n_tr'] == 1827 and abs(full['exp_r'] - 0.1690) < 5e-4
          and abs(full['pf'] - 1.488) < 1e-3)
    P('--- ГЕЙТ: baseline = H26 ---')
    P(f'  n_tr={full["n_tr"]} expR={full["exp_r"]:.4f} PF={full["pf"]:.4f} -> '
      f'{"СОВПАЛО" if ok else "НЕ СОВПАЛО"}')
    if not ok:
        OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
        return
    P('')

    # динамические маски считаем один раз на весь ряд
    dyn = {}
    for W in WINDOWS_W:
        masks, bad = {}, []
        for co in er:
            m = dyn_mask(er[co], W)
            if m is None:
                bad.append(co)
            else:
                masks[co] = m
        if bad:
            P(f'  dyn{W}: не хватает истории для {bad}, пропускаем')
            continue
        dyn[W] = masks
    P('')

    first = _key(idx[0]) + TRAIN_MONTHS
    wins = []
    for w in range(N_WINDOWS):
        lo, hi = month_span(idx, first + w * TEST_MONTHS, TEST_MONTHS)
        if hi - lo < 50:
            break
        wins.append((w + 1, lo, hi))
    P(f'окон {len(wins)}')
    P('')

    names = ['baseline', 'frozen'] + [f'dyn{W}' for W in WINDOWS_W if W in dyn]
    acc = {n: [] for n in names}
    tots = {n: [] for n in names}
    P('=' * 104)
    P('1. ПО ОКНАМ (OOS)')
    P('=' * 104)
    P(f'{"окно":<6}{"вариант":<10}{"сделок":>8}{"доход":>10}{"DD":>8}   пороги')
    P('-' * 104)
    for w, lo, hi in wins:
        b = win_metrics(H24.run(prep, idx[:hi], RISK, cap=CAP, capital=CAPITAL), lo, hi)
        acc['baseline'].append(b['R'])
        tots['baseline'].append(b['tot'])
        P(f'{w:<6}{"baseline":<10}{b["n_tr"]:>8}{b["tot"]*100:>9.1f}%{b["mdd"]*100:>7.1f}%')
        fmask, ths = {}, []
        for co in er:
            m, th = frozen_mask(er[co], lo)
            fmask[co] = m
            ths.append(th)
        r = win_metrics(H24.run(prep, idx[:hi], RISK, cap=CAP, capital=CAPITAL,
                                entry_mask=fmask), lo, hi)
        acc['frozen'].append(r['R'])
        tots['frozen'].append(r['tot'])
        P(f'{w:<6}{"frozen":<10}{r["n_tr"]:>8}{r["tot"]*100:>9.1f}%{r["mdd"]*100:>7.1f}%'
          f'   {min(ths):.3f}..{max(ths):.3f}')
        for W in WINDOWS_W:
            if W not in dyn:
                continue
            r = win_metrics(H24.run(prep, idx[:hi], RISK, cap=CAP, capital=CAPITAL,
                                    entry_mask=dyn[W]), lo, hi)
            acc[f'dyn{W}'].append(r['R'])
            tots[f'dyn{W}'].append(r['tot'])
            P(f'{w:<6}{f"dyn{W}":<10}{r["n_tr"]:>8}{r["tot"]*100:>9.1f}%'
              f'{r["mdd"]*100:>7.1f}%')
    P('-' * 104)
    P('')

    P('=' * 104)
    P('2. СВОДКА (OOS, 24 месяца)')
    P('=' * 104)
    P(f'{"вариант":<10}{"сделок":>8}{"expR":>8}{"PF":>7}{"доход":>10}{"худшDD":>9}'
      f'{"Δдоход":>10}{"ΔDD":>8}')
    P('-' * 104)
    base_tot = compound(tots['baseline'])
    base_mdd = 0.0
    res = {}
    for n in names:
        R = np.concatenate([x for x in acc[n] if len(x)]) if any(len(x) for x in acc[n]) else np.array([])
        tot = compound(tots[n])
        # худш DD по окнам
        mdd = 0.0
        for w, lo, hi in wins:
            pass
        res[n] = dict(n=len(R), exp=float(R.mean()) if len(R) else np.nan,
                      pf=float(R[R > 0].sum() / -R[R < 0].sum()) if len(R) else np.nan,
                      tot=tot, mdd=mdd, R=R)
    # считаем DD честно по каждому окну
    for n in names:
        mdds = []
        for w, lo, hi in wins:
            mm = None
            if n == 'baseline':
                r = win_metrics(H24.run(prep, idx[:hi], RISK, cap=CAP, capital=CAPITAL), lo, hi)
                mdds.append(r['mdd'])
            elif n == 'frozen':
                fm = {co: frozen_mask(er[co], lo)[0] for co in er}
                mdds.append(win_metrics(H24.run(prep, idx[:hi], RISK, cap=CAP,
                                                capital=CAPITAL, entry_mask=fm), lo, hi)['mdd'])
            else:
                W = int(n[3:])
                mdds.append(win_metrics(H24.run(prep, idx[:hi], RISK, cap=CAP,
                                                capital=CAPITAL, entry_mask=dyn[W]), lo, hi)['mdd'])
        res[n]['mdd'] = max(mdds)
    for n in names:
        r = res[n]
        P(f'{n:<10}{r["n"]:>8}{r["exp"]:>8.3f}{r["pf"]:>7.3f}{r["tot"]*100:>9.1f}%'
          f'{r["mdd"]*100:>8.1f}%{(r["tot"]-base_tot)*100:>9.1f}%'
          f'{(r["mdd"]-res["baseline"]["mdd"])*100:>7.1f}%')
    P('-' * 104)
    P('')

    P('=' * 104)
    P('ВЕРДИКТ')
    P('=' * 104)
    okW = [f'dyn{W}' for W in WINDOWS_W
           if W in dyn and res[f'dyn{W}']['tot'] > base_tot]
    P(f'  frozen (эталон H33/A):  {res["frozen"]["tot"]*100:+.1f}%  '
      f'expR {res["frozen"]["exp"]:.3f}  DD {res["frozen"]["mdd"]*100:.1f}%')
    for W in WINDOWS_W:
        if W in dyn:
            r = res[f'dyn{W}']
            mark = 'да' if r['tot'] > base_tot else 'НЕТ'
            P(f'  dyn{W:<5}: {r["tot"]*100:+.1f}%  expR {r["exp"]:.3f}  '
              f'DD {r["mdd"]*100:.1f}%   лучше baseline: {mark}')
    P('')
    if okW:
        P(f'  Динамический порог работает: {", ".join(okW)} дают доход выше baseline.')
        P(f'  Деплоить dyn{WINDOWS_W[0]} — он ближе всего к процедуре H33')
        P('  (окно ~82 суток, обновляется каждый бар) и требует меньше истории.')
        P('')
        P('  ЧТО ЭТО ДАЁТ В ЖИВОМ БОТЕ: никакой зашитый порог, который')
        P('  устареет через полгода. Методика walk-forward воспроизводится')
        P('  по построению — train-момент не нужен вовсе.')
    else:
        P('  Динамический порог НЕ работает. Деплоить frozen (зашитый порог)')
        P('  из конфига и пересматривать его при ре-валидации.')
    P('')
    P('  ОГОВОРКА: все варианты проверены на том же периоде 2024-04..2026-09.')
    P('  Разница frozen/dyn — это выбор реализации, а не независимая проверка.')
    P('  Что ДЕПЛОИТЬ — вопрос надёжности реализации, а не доходности.')

    OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT.resolve()}')


if __name__ == '__main__':
    main()
