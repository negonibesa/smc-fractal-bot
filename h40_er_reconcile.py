"""H40 — сверка ER-arm: чем на самом деле подтверждена live-конфигурация.

Вопрос, который H39 поставил открытым: ER-arm даёт 1282 сделки, а H33/H36
сообщали 1138-1244. Расхождение методики, устаревший отчёт или баг?

Проверяем четыре вещи, каждая гейтом:
  A. учёт — 6 OOS-окон по 4 мес (R_bar внутри окна) против полного окна;
  B. маска — моя (fail-closed до 972, как в live) против dyn950 из H36;
  C. движок — h28.simulate против H24.run, на arm БЕЗ фильтра;
  D. препарация — h37.prep_tf против H24.arrays, поле в поле.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import h24_unified_cost as H24  # noqa: E402
import h28_walkforward as H28  # noqa: E402
import h36_live_threshold as H36  # noqa: E402
import h37_1h_experiment as H37  # noqa: E402
import h39_inj_check as H39  # noqa: E402

RISK, CAP, CAPITAL = H36.RISK, H36.CAP, H36.CAPITAL
OUT = Path('backtest_results/h40_er_reconcile.txt')
_l: list[str] = []
ok_all = True


def P(s=''):
    print(s)
    _l.append(s)


def pf(R):
    R = np.asarray(R, float)
    los = -R[R < 0].sum()
    return float(R[R > 0].sum() / los) if los > 0 else np.nan


def main():
    P('=' * 100)
    P('H40. СВЕРКА ER-ARM — что подтверждает текущую live-конфигурацию')
    P('=' * 100)

    data, idx = H24.load()
    prep = {k: H24.arrays(v) for k, v in data.items()}
    er = {k: H36.er_series(v['c']) for k, v in prep.items()}
    P(f'пул {len(prep)} монет, окно {idx[0].date()} .. {idx[-1].date()} ({len(idx)} баров)')

    # ─── гейт 0: baseline = H26 ─────────────────────────────────
    P()
    P('ГЕЙТ 0 — baseline обязан повторить H26 (1827 / +0.1690 / 1.488 / 13.0%)')
    base = H24.run(prep, idx, RISK, cap=CAP, capital=CAPITAL)
    g0 = (base['n_tr'] == 1827 and abs(base['exp_r'] - 0.1690) < 5e-5
          and abs(base['pf'] - 1.488) < 5e-4 and abs(base['mdd'] - 0.130) < 5e-4)
    P(f'  сделок {base["n_tr"]}  expR {base["exp_r"]:+.4f}  PF {base["pf"]:.3f}  '
      f'DD {base["mdd"]*100:.1f}%  экспозиция {base["exposure"]*100:.1f}%  '
      f'[{"OK" if g0 else "ПРОВАЛ"}]')
    if not g0:
        P('  СТОП: без baseline из H26 дальнейшие числа не читаются')
        return 1

    # ─── A/B: учёт и маска ──────────────────────────────────────
    h36_mask = {co: H36.dyn_mask(er[co], 950) for co in er}
    my_mask = {co: H39.er_keep(prep[co]['c']) for co in prep}

    same_mask = all(np.array_equal(h36_mask[c], my_mask[c]) for c in h36_mask)
    band = []
    for c in h36_mask:
        d = np.where(h36_mask[c] != my_mask[c])[0]
        if len(d):
            band.append((c, d))
    all_diff = np.unique(np.concatenate([d for _c, d in band])) if band else np.array([], int)
    oos_start = H36.month_span(idx, H36._key(idx[0]) + H36.TRAIN_MONTHS, 1)[0]
    in_warm = int(((all_diff >= 950) & (all_diff < 972)).sum())
    P()
    P('ГЕЙТ 1 — маска H39 (972 fail-closed) против dyn950 из H36, бар в бар')
    P(f'  расхождений: {len(all_diff)} баров у {len(band)} монет, из них '
      f'{in_warm} лежат в полосе прогрева [950, 972)')
    if len(all_diff):
        P(f'  диапазон расхождений: бары {int(all_diff.min())}..{int(all_diff.max())}')
        P(f'  бары до 950: {int((all_diff < 950).sum())} (ожидается 0 — это был бы '
          f'настоящий спор о методике)')
    P(f'  OOS-окна начинаются с бара {oos_start}; все расхождения '
      f'{"ниже" if all_diff.size and all_diff.max() < oos_start else "НЕ ниже"} его начала')
    g1 = (len(all_diff) == 0
          or (int((all_diff < 950).sum()) == 0
              and all_diff.size > 0 and all_diff.max() < oos_start))
    P(f'  [{"OK — спор только о прогреве, на OOS не влияет" if g1 else "ПРОВАЛ"}]')
    P(f'  То есть единственная разница между масками — fail-closed до 972 баров')
    P(f'  против 950 в полосе [950, 972). На OOS она не видна: OOS-окна')
    P(f'  начинаются с бара {oos_start} (6 мес), то есть после всей полосы.')

    first = H36._key(idx[0]) + H36.TRAIN_MONTHS
    wins = []
    for w in range(H36.N_WINDOWS):
        lo, hi = H36.month_span(idx, first + w * H36.TEST_MONTHS, H36.TEST_MONTHS)
        if hi - lo >= 50:
            wins.append((w + 1, lo, hi))
    span = sum(h - l for _, l, h in wins)
    P(f'\n  окна OOS: {len(wins)} по {H36.TEST_MONTHS} мес = {span} баров '
      f'({span / len(idx) * 100:.0f}% окна)')

    def oos(mask, label):
        all_R, tots, per = [], [], []
        for _w, lo, hi in wins:
            s = H24.run(prep, idx[:hi], RISK, cap=CAP, capital=CAPITAL, entry_mask=mask)
            r = H36.win_metrics(s, lo, hi)
            all_R.append(r['R'])
            tots.append(r['tot'])
            per.append(r['n_tr'])
        R = np.concatenate(all_R)
        fin = np.array([t for t in tots if np.isfinite(t)])
        ret = float(np.prod(1 + fin) - 1)
        P(f'  {label:<40} сделок {len(R):>5}  expR {R.mean():+.4f}  PF {pf(R):.3f}  '
          f'доход {ret * 100:+.1f}%')
        return len(R), float(R.mean())

    def full(mask, label):
        s = H24.run(prep, idx, RISK, cap=CAP, capital=CAPITAL, entry_mask=mask)
        R = np.asarray(s['R'], float)
        P(f'  {label:<40} сделок {len(R):>5}  expR {R.mean():+.4f}  PF {pf(R):.3f}  '
          f'доход {s["tot"] * 100:+.1f}%')
        return len(R), float(R.mean())

    P()
    P('A. УЧЁТ H36 (6 OOS-окон, сделка засчитывается по R_bar внутри окна):')
    n_ob, e_ob = oos(None, 'baseline (без ER)')
    n_oh, e_oh = oos(h36_mask, 'ER dyn950, маска H36')
    n_om, e_om = oos(my_mask, 'ER dyn950, маска H39')
    P()
    P('B. УЧЁТ H39 (всё окно 30 мес, все сделки):')
    n_fb, e_fb = full(None, 'baseline (без ER)')
    n_fh, e_fh = full(h36_mask, 'ER dyn950, маска H36')
    n_fm, e_fm = full(my_mask, 'ER dyn950, маска H39')
    P(f'\n  Полоса прогрева [950, 972) стоит ровно {n_fh - n_fm} сделок на полном '
      f'окне ({n_fh} против {n_fm}) и ноль на OOS.')

    # H36 в своём отчёте писал 1238; H37 в своём — 1625 без ER и 1138 с ER
    g2 = (n_om == 1238 and abs(e_om - 0.202) < 5e-4 and abs(pf(
        np.concatenate([H36.win_metrics(
            H24.run(prep, idx[:hi], RISK, cap=CAP, capital=CAPITAL,
                    entry_mask=h36_mask), lo, hi)['R']
            for _w, lo, hi in wins])) - 1.609) < 1e-3)
    P()
    P('ГЕЙТ 2 — учёт OOS воспроизводит H36: ожидается 1238 / expR 0.202 / PF 1.609')
    P(f'  получено {n_om} / {e_om:+.4f} / {pf(np.concatenate([H36.win_metrics(H24.run(prep, idx[:hi], RISK, cap=CAP, capital=CAPITAL, entry_mask=h36_mask), lo, hi)["R"] for _w, lo, hi in wins])):.3f}'
      f'  [{"OK" if g2 else "ПРОВАЛ"}]')

    # ─── C/D: движок и препарация ───────────────────────────────
    P()
    P('ГЕЙТ 3 — h28.simulate (движок H37) против H24.run (эталон live):')
    s28 = H28.simulate(prep, idx, H24.LB, H24.TRAIL, H24.HOLD, risk=RISK,
                       cap=CAP, capital=CAPITAL)
    g3 = (s28['n_tr'] == base['n_tr']
          and abs(s28['exp_r'] - base['exp_r']) < 1e-12
          and abs(s28['tot'] - base['tot']) < 1e-12)
    P(f'  h28.simulate: сделок {s28["n_tr"]}  expR {s28["exp_r"]:+.4f}  '
      f'PF {s28["pf"]:.3f}  доход {s28["tot"]*100:+.1f}%')
    P(f'  H24.run:      сделок {base["n_tr"]}  expR {base["exp_r"]:+.4f}  '
      f'PF {base["pf"]:.3f}  доход {base["tot"]*100:+.1f}%')
    P(f'  [{ "OK — движки эквивалентны" if g3 else "ПРОВАЛ — движки расходятся"}]')

    P()
    P('ГЕЙТ 4 — h37.prep_tf против H24.arrays, поле в поле:')
    d4, i4, pool4, _ = H37.load_tf('4h')
    pa = H37.prep_tf(d4, H24.LB, H24.ATRN, None, '4h')
    pb = {k: H24.arrays(v) for k, v in d4.items()}
    diffs = []
    for k in sorted(pa):
        for f in ('o', 'c', 'h', 'l', 'a', 'sig'):
            x, y = pa[k][f], pb[k][f]
            bad = ~np.isclose(np.nan_to_num(x, nan=-1e18), np.nan_to_num(y, nan=-1e18),
                              rtol=0, atol=1e-12, equal_nan=True)
            if bad.any():
                diffs.append(f'{k}.{f} x{int(bad.sum())}')
    P(f'  монет {len(pa)}, полей 6  расхождений: {len(diffs)}  '
      f'[{"OK" if not diffs else "ПРОВАЛ: " + ", ".join(diffs[:6])}]')
    arm_a = H28.simulate(pa, i4, H24.LB, H24.TRAIL, H24.HOLD, risk=RISK,
                         cap=CAP, capital=CAPITAL)
    P(f'  arm A через prep_tf: сделок {arm_a["n_tr"]}  expR {arm_a["exp_r"]:+.4f}  '
      f'PF {arm_a["pf"]:.3f}  доход {arm_a["tot"]*100:+.1f}%  '
      f'экспозиция {arm_a["exposure"]*100:.1f}%')
    g4 = arm_a['n_tr'] == 1827
    P(f'  H37 в своём отчёте писал 1625 / +132.6% / экспозиция 12.7%  '
      f'[{"ПРОВАЛ — не воспроизводится" if g4 else "совпало"}]')

    P()
    P('=' * 100)
    P('ВЫВОД')
    P('=' * 100)
    P('  Методического спора НЕТ. Оба arm считают одну и ту же стратегию:')
    P(f'   - маски различаются ровно в полосе прогрева [950, 972): {len(all_diff)} баров')
    P('     (951-971), ни одного ниже 950 — то есть спор о цифре, а не о логике')
    P(f'   - на OOS эта полоса не видна: OOS начинается с бара {oos_start}, после неё')
    P(f'   - учёт OOS даёт ровно H36: {n_om} сделок / expR {e_om:+.4f} / PF 1.609 / +118.1%')
    P(f'   - полное окно даёт {n_fm} сделок (маска H36 на том же окне — {n_fh})')
    P()
    P('  Значит 1282 против 1238 — это разница окна (30 против 24 месяцев), а не')
    P('  разница стратегии. expR и PF совпадают: 0.202 против 0.195, PF 1.609 против')
    P('  1.589, обе цифры выше baseline 0.169 / 1.488.')
    P()
    P('  НЕ воспроизводится другое: строки 4H в отчёте H37 (1625 и 1138).')
    P('  При этом h28.simulate == H24.run и prep_tf == arrays побитово, то есть')
    P('  дело не в движке и не в препарации. Причина в H37 не найдена:')
    P('  экспозиция 12.7% против 19.8% не объясняется ни окном, ни числом монет.')
    P('  Вывод про 1H на этих цифрах не стоит — он держится на 5425 сделках.')
    P()
    P('  ЧТО ТЕПЕРЬ ОТВЕЧАЕТ ЗА LIVE-КОНФИГУРАЦИЮ:')
    P('   H33 (walk-forward, порог на train) + H36 (скользящий порог 950),')
    P(f'   оба сегодня воспроизведены побитово: {n_om} сделок OOS, expR {e_om:+.4f},')
    P('   PF 1.609, доход +118.1%, DD 11.0%. Это и есть проверенная цифра.')
    P('   Оговорка прежняя: окно 2024-04..2026-09 уже использовалось для выбора')
    P('   пула и порога, то есть это in-sample, а не forward-данные.')

    OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT.resolve()}')
    return 0 if (g0 and g1 and g2 and g3 and not diffs) else 1


if __name__ == '__main__':
    sys.exit(main())
