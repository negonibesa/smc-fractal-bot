"""H32 — объясняет ли режим рынка P&L нашего Donchian?

Это проверка гипотезы «адаптивный бот», а не постройка адаптивного бота.
Всё, что нужно от HMM — это доказать, что P&L зависит от режима. Если
зависимости нет, никакая HMM-изощрённость её не создаст.

Метка режима намеренно ПРОЗРАЧНАЯ, без HMM и без подбора:
  волатильность  = ATR(20)/close, порог — медиана по всем барам пула
  сила тренда     = Kaufman Efficiency Ratio(20) = |c-c[-20]| / sum|Δc| за 20
                    ER≈1 — чистый тренд, ER≈0 — хаос. Порог — медиана.
Это 2 свободных порога вместо параметров HMM. Если P&L различается даже так,
гипотеза живая и можно тратить степени свободы на HMM. Если нет — тема закрыта.

Метка ставится на баре ВХОДА (R_ei), потому что адаптивный бот решал бы
до входа, а не после.

Паритет: до всех выводов проверяем, что портфель воспроизводит H26.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import h24_unified_cost as H24  # noqa: E402

POOL = ['ADA', 'ARB', 'DOGE', 'ENA', 'HBAR', 'LINK', 'NEAR', 'SUI', 'XLM', 'XRP']
H24.POOL_PIN = POOL          # путь зафиксирован, data/raw больше не влияет
ER_N = 20
OUT = Path('backtest_results/h32_regime.txt')
_l: list[str] = []


def P(s=''):
    print(s)
    _l.append(s)


def regime_labels(d: pd.DataFrame, atrn=20, er_n=ER_N):
    """(волатильность, ER) на каждом баре. Только backward — без lookahead."""
    c = d['close'].to_numpy(float)
    h, l = d['high'].to_numpy(float), d['low'].to_numpy(float)
    a = H24.true_range(np.array(d['open'], float), h, l, c)
    vol = pd.Series(a).rolling(atrn).mean().to_numpy() / c
    # Kaufman ER: чистое перемещение / суммарный путь
    net = np.abs(c - np.roll(c, er_n))
    net[:er_n] = np.nan
    path = pd.Series(np.abs(np.diff(c, prepend=c[0]))).rolling(er_n).sum().to_numpy()
    er = net / np.where(path > 0, path, np.nan)
    return vol, er


def tstat_diff(a, b):
    """t-статистика различия средних (Welch). Возвращает (t, p_approx)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 5 or len(b) < 5:
        return np.nan, np.nan
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = np.sqrt(va / len(a) + vb / len(b))
    if se == 0:
        return np.nan, np.nan
    t = (a.mean() - b.mean()) / se
    # df по Уэлчу
    df = (va / len(a) + vb / len(b)) ** 2 / (
        (va / len(a)) ** 2 / (len(a) - 1) + (vb / len(b)) ** 2 / (len(b) - 1))
    try:
        from scipy import stats
        return t, float(2 * (1 - stats.t.cdf(abs(t), df)))
    except Exception:
        return t, np.nan


def main():
    data, idx = H24.load()
    prep = {k: H24.arrays(v) for k, v in data.items()}
    r = H24.run(prep, idx, 0.0035)

    P('=' * 100)
    P('H32. РЕЖИМНАЯ ОБУСЛОВЛЕННОСТЬ P&L (гипотеза «адаптивный бот»)')
    P('=' * 100)
    P(f'окно {idx[0].date()} .. {idx[-1].date()}  ({len(idx)} баров 4H, {len(POOL)} монет)')
    P('')
    P('--- ГЕЙТ 1: портфель воспроизводит H26 ---')
    ok = (r['n_tr'] == 1827 and abs(r['exp_r'] - 0.1690) < 5e-4
          and abs(r['pf'] - 1.488) < 1e-3 and abs(r['tot'] - 1.585) < 5e-3
          and abs(r['mdd'] - 0.1304) < 1e-3)
    P(f'  n_tr={r["n_tr"]} expR={r["exp_r"]:.4f} PF={r["pf"]:.4f} '
      f'tot={r["tot"]:.4f} mdd={r["mdd"]:.4f}   -> {"СОВПАЛО" if ok else "НЕ СОВПАЛО"}')
    if not ok:
        P('  гейт провален, выводы ниже не имеют смысла')
        OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
        return
    P('')

    R = r['R']
    syms = list(r['R_sym'])
    eis = list(r['R_ei'])

    # --- метки режима на баре входа каждой сделки
    lab_vol, lab_er = [], []
    cache = {}
    for co, ei in zip(syms, eis):
        if co not in cache:
            cache[co] = regime_labels(data[co])
        v, e = cache[co]
        lab_vol.append(v[ei] if ei < len(v) else np.nan)
        lab_er.append(e[ei] if ei < len(e) else np.nan)
    lab_vol = np.array(lab_vol, float)
    lab_er = np.array(lab_er, float)

    # --- пороги: медиана по ВСЕМ барам пула (не по сделкам — иначе подгонка)
    allv, alle = [], []
    for co in cache:
        v, e = cache[co]
        allv.append(v)
        alle.append(e)
    allv = np.concatenate(allv)
    alle = np.concatenate(alle)
    vth, eth = np.nanmedian(allv), np.nanmedian(alle)

    P('--- ГЕЙТ 2: метки определены на всех сделках ---')
    miss = int(np.isnan(lab_vol).sum() + np.isnan(lab_er).sum())
    P(f'  сделок {len(R)}, пропусков меток {miss}')
    P(f'  порог волатильности (медиана ATR20/close) = {vth*100:.3f}%')
    P(f'  порог силы тренда  (медиана ER{ER_N})      = {eth:.3f}')
    P('')

    P('=' * 100)
    P('1. P&L ПО 4 РЕЖИМАМ (волатильность × сила тренда, пороги — медианы)')
    P('=' * 100)
    P(f'{"режим":<34}{"сделок":>7}{"expR":>8}{"PF":>7}{"WR%":>7}{"суммаR":>9}')
    P('-' * 100)
    cells = {}
    for vh in (True, False):
        for eh in (True, False):
            m = ((lab_vol >= vth) if vh else (lab_vol < vth))
            m &= ((lab_er >= eth) if eh else (lab_er < eth))
            m &= ~np.isnan(lab_vol) & ~np.isnan(lab_er)
            Rs = R[m]
            if len(Rs):
                w, l = Rs[Rs > 0].sum(), -Rs[Rs < 0].sum()
                pf = w / l if l > 0 else np.inf
            else:
                pf = np.nan
            name = f'{"волат" if vh else "тихо"} × {"тренд" if eh else "хаос"}'
            cells[(vh, eh)] = (Rs, name)
            pfs = 'inf' if pf == np.inf or (pf == pf and pf > 9) else f'{pf:.3f}'
            P(f'{name:<34}{len(Rs):>7}{Rs.mean() if len(Rs) else np.nan:>8.3f}'
              f'{pfs:>7}{(Rs > 0).mean()*100 if len(Rs) else np.nan:>6.1f}%'
              f'{Rs.sum() if len(Rs) else 0:>9.1f}')
    P('-' * 100)
    P('')

    P('=' * 100)
    P('2. РАЗЛИЧАЕТСЯ ЛИ P&L? (Welch t-тест на разности средних R)')
    P('=' * 100)
    P(f'{"сравнение":<40}{"expR A":>9}{"expR B":>9}{"разн":>9}{"t":>8}{"p":>9}')
    P('-' * 100)
    tests = [
        ('волатильность: волатильная vs тихая',
         cells[(True, True)][0].tolist() + cells[(True, False)][0].tolist(),
         cells[(False, True)][0].tolist() + cells[(False, False)][0].tolist()),
        ('тренд: трендовый vs хаотичный',
         cells[(True, True)][0].tolist() + cells[(False, True)][0].tolist(),
         cells[(True, False)][0].tolist() + cells[(False, False)][0].tolist()),
        ('тренд внутри тихого рынка',
         cells[(False, True)][0].tolist(), cells[(False, False)][0].tolist()),
        ('тренд внутри волатильного рынка',
         cells[(True, True)][0].tolist(), cells[(True, False)][0].tolist()),
    ]
    sig = []
    for name, a, b in tests:
        a, b = np.array(a, float), np.array(b, float)
        t, p = tstat_diff(a, b)
        P(f'{name:<40}{a.mean() if len(a) else np.nan:>9.3f}'
          f'{b.mean() if len(b) else np.nan:>9.3f}'
          f'{(a.mean()-b.mean()) if len(a) and len(b) else np.nan:>9.3f}'
          f'{t:>8.2f}{p:>9.4f}')
        if p == p and p < 0.05:
            sig.append(name)
    P('-' * 100)
    P('')

    P('=' * 100)
    P('3. ЭКОНОМИЧЕСКИЙ СМЫСЛ: сколько R отнимает фильтр')
    P('=' * 100)
    best = max(cells.items(), key=lambda kv: (kv[1][0].mean() if len(kv[1][0]) else -9))
    keep = best[0]
    Rs_keep = best[1][0]
    P(f'лучший режим: {best[1][1]}  expR {Rs_keep.mean():+.3f} на {len(Rs_keep)} сделках')
    P(f'если торговать ТОЛЬКО в нём: {len(Rs_keep)} из {len(R)} сделок '
      f'({len(Rs_keep)/len(R)*100:.0f}%), сумма R {Rs_keep.sum():+.1f} против {R.sum():+.1f}')
    P('')
    P('сколько R в среднем приносит каждая сделка ВНЕ лучшего режима:')
    out_mask = ~((lab_vol >= vth) if keep[0] else (lab_vol < vth))
    out_mask &= ~((lab_er >= eth) if keep[1] else (lab_er < eth))
    out_mask &= ~np.isnan(lab_vol) & ~np.isnan(lab_er)
    Ro = R[out_mask]
    if len(Ro):
        P(f'  вне режима: {len(Ro)} сделок, expR {Ro.mean():+.3f}, сумма {Ro.sum():+.1f}')
    P('')

    P('=' * 100)
    P('ВЕРДИКТ')
    P('=' * 100)
    if sig:
        P('  Различия значимы (p < 0.05): ' + '; '.join(sig))
        P('  -> гипотеза «режим объясняет P&L» ЖИВА. Следующий шаг — посмотреть,')
        P('     даёт ли фильтр по режиму прирост на OOS (а не только различает выборку).')
    else:
        P('  НИ ОДНО различие не значимо (p >= 0.05).')
        P('  -> гипотеза «режим объясняет P&L» МЕРТВА даже в такой грубой форме.')
        P('     HMM не имеет смысла: улучшать нечего, P&L не зависит от режима.')
    P('')
    P('  ОГОВОРКА: это тот же in-sample период 2024-04..2026-09. Отсутствие')
    P('  значимой разницы — сильный аргумент против HMM. Наличие разницы —')
    P('  слабый аргумент за: её надо сначала проверить на OOS, иначе это H6.')

    OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT.resolve()}')


if __name__ == '__main__':
    main()
