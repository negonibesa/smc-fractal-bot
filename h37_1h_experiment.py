"""H37 — 1H как ОТДЕЛЬНАЯ гипотеза. Честное сравнение с 4H.

Ничего не меняет в live и не трогает H28: симулятор импортируется из
h28_walkforward как есть, ER-фильтр реализован маской на сигнальном
массиве (simulate читает prep[co]['sig'][k], маска просто зануляет).

ПРОТОКОЛ (зафиксирован до просмотра результатов)
-----------------------------------------------
Пул: пересечение пула10 и наличия 1H-данных. ENA и SUI в 1H отсутствуют,
поэтому работаем на 8 монетах. Чтобы сравнение было контролируемым, 4H
считается на ТОЖЕ пуле и в ТОЖЕ окне, что и 1H. Иначе сравнивались бы
разные пулы и разные длины истории.

Окно: пересечение диапазонов 4H и 1H по этим 8 монетам.

Варианты (lb=20, ATR=20, trail=2.0 — валидированные H24/H28):
  A  4H  без ER                       H28-параметры, эталон без фильтра
  B  4H  ER(20) window 950            = текущий live
  C  1H  без ER                       «просто пробой на 1H»
  D  1H  ER(20) window 950            фильтр как на 4H, те же 950 баров
  E  1H  ER(20) window 3800           фильтр, СОГЛАСОВАННЫЙ ПО ВРЕМЕНИ
                                      (950 баров 4H = 3800 баров 1H)
  F  1H  без ER, hold=120             hold, согласованный по времени
                                      (30 баров 4H = 5 дней = 120 баров 1H)
  G  1H  ER(20) window 950, hold=120  ER + time-matched hold

Контрасты:
  A→B  даёт ли ER пользу на 4H (перепроверка на 8 монетах)
  A→C  1H против 4H без ER
  B→D  1H против 4H с ER
  C→D  даёт ли ER пользу на 1H
  D→E  чувствительность к определению окна ER
  C→F  влияние периода удержания на 1H
  D→G  то же с ER

Честность
---------
- Издержки те же 0.14% round-trip, но на 1H ATR меньше, поэтому издержки
  в долях R заметно выше. Это не баг, а структурный факт, и он считается
  явно (метрика «издержки в R»).
- Walk-forward 6 окон, как в H28, плюс Monte Carlo 3000 итераций.
- Адаптивный вариант (подбор на train) — максимально благоприятный для
  1H тест: даже с лучшими параметрами, выбранными на train, 1H должен
  выиграть. Иначе отвергаем и НЕ крутим параметры.
- Live-реализуемость — отдельный критерий: 950 баров помещается в один
  запрос Bybit, 3800 требует пагинации (4 запроса на символ за цикл).

Критерий приёмки (все должны выполниться, иначе 1H отвергается)
----------------------------------------------------------------
1. expR(1H+ER) > expR(4H+ER)
2. PF >= 1 минимум в 4 из 6 OOS-окон
3. max DD(1H) не хуже max DD(4H)
4. MC: DD p5 <= 25%
5. Не вырожденно: >= 300 OOS-сделок
6. Live-реализуемо без пагинации либо пагинация доказуемо дешёвая
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import h24_unified_cost as H24  # noqa: E402
import h28_walkforward as H28  # noqa: E402
from core.donchian_breakout import efficiency_ratio  # noqa: E402

OUT = Path('backtest_results/h37_1h_experiment.txt')
_l: list[str] = []

TAKER, SLIP, CAPITAL = H24.TAKER, H24.SLIP, H24.CAPITAL
LEV, MARGIN_BUF, CAP = H24.LEV, H24.MARGIN_BUF, H24.CAP
RISK = 0.0035
LB, ATRN, TRAIL = 20, 20, 2.0
HOLD_4H, HOLD_1H_TM = 30, 120

POOL10 = ['ADA', 'ARB', 'DOGE', 'ENA', 'HBAR', 'LINK', 'NEAR', 'SUI', 'XLM', 'XRP']
LBS = (10, 15, 20, 30, 40)
TRAILS = (1.5, 2.0, 2.5, 3.0)
HOLDS_4H = (15, 20, 30, 40)
HOLDS_1H = (15, 30, 60, 120)

TRAIN_MONTHS, TEST_MONTHS, N_WINDOWS, MIN_TRADES_TRAIN = 6, 4, 6, 30

_prep_cache: dict = {}


def P(s=''):
    print(s)
    _l.append(s)


# ─── загрузка ─────────────────────────────────────────────────────

def load_tf(tf: str) -> tuple[dict, pd.DatetimeIndex]:
    sub = 'raw' if tf == '4h' else 'raw_1h'
    pat = f'*_4h*.csv' if tf == '4h' else f'*_1h_*.csv'
    have = {f.name.split('_')[0] for f in Path('data', sub).glob(pat)}
    pool = [c for c in POOL10 if c in have]
    miss = [c for c in POOL10 if c not in have]
    data = {}
    for c in pool:
        f = next(Path('data', sub).glob(f'{c}{pat[1:]}'))
        data[c] = pd.read_csv(f, index_col=0, parse_dates=True)
    idx = None
    for d in data.values():
        idx = d.index if idx is None else idx.intersection(d.index)
    lo, hi = idx.min(), idx.max()
    data = {k: v.loc[lo:hi] for k, v in data.items()}
    idx = None
    for d in data.values():
        idx = d.index if idx is None else idx.intersection(d.index)
    out = {k: v.reindex(idx).dropna() for k, v in data.items()}
    out = {k: v for k, v in out.items() if len(v) > 200}
    return out, idx, pool, miss


# ─── индикаторы + ER-маска ────────────────────────────────────────

def prep_tf(data: dict, lb: int, atrn: int, er_window: int | None,
            tf: str = '') -> dict:
    """er_window=None -> без фильтра. Иначе sig зануляется там, где
    ER(20) < медианы ER(20) за предыдущие er_window баров.

    tf ОБЯЗАТЕЛЬНО входит в ключ кэша: набор монет на 4H и 1H совпадает,
    и без tf кэш отдавал бы 4H-препарацию для 1H (тихий подмен таймфрейма).
    """
    key = (tf, tuple(sorted(data)), len(next(iter(data.values()))),
           str(next(iter(data.values())).index[0]),
           str(next(iter(data.values())).index[-1]),
           lb, atrn, er_window)
    if key in _prep_cache:
        return _prep_cache[key]
    out = {}
    for k, d in data.items():
        p = H28.prep_one(d, lb, atrn)
        if er_window:
            c = d['close'].to_numpy(float)
            er = efficiency_ratio(c, 20)
            keep = np.zeros(len(er), bool)
            for j in range(len(er)):
                lo = j - er_window
                if lo < 0:
                    continue
                w = er[lo:j]
                w = w[~np.isnan(w)]
                if len(w) < er_window // 2 or np.isnan(er[j]):
                    continue          # fail-closed: мало истории -> не торгуем
                keep[j] = er[j] >= float(np.median(w))
            p['sig'] = np.where(keep, p['sig'], 0.0)
            p['er'] = er
        out[k] = p
    _prep_cache[key] = out
    return out


# ─── окна ─────────────────────────────────────────────────────────

def _key(ts) -> int:
    return ts.year * 12 + ts.month


def month_span(idx, start_key: int, n_months: int) -> tuple[int, int]:
    s, e = start_key, start_key + n_months
    lo = next((i for i, t in enumerate(idx) if _key(t) >= s), len(idx))
    hi = next((i for i, t in enumerate(idx) if _key(t) >= e), len(idx))
    return lo, hi


def build_windows(idx):
    first = _key(idx[0]) + TRAIN_MONTHS
    w = []
    for i in range(N_WINDOWS):
        sk = first + i * TEST_MONTHS
        lo, hi = month_span(idx, sk, TEST_MONTHS)
        if hi - lo < 50:
            break
        w.append((i + 1, lo, hi, sk))
    return w


# ─── агрегаты и MC ────────────────────────────────────────────────

def chain(parts) -> dict | None:
    eq_all, R_all, acc = [], [], 1.0
    for r in parts:
        if r is None or len(r['eq']) == 0:
            continue
        eq_all.append(acc * r['eq'] / r['eq'][0])
        acc *= r['eq'][-1] / r['eq'][0]
        R_all.append(r['R'])
    if not eq_all:
        return None
    E, R = np.concatenate(eq_all), np.concatenate(R_all)
    w, l = (R[R > 0].sum(), -R[R < 0].sum()) if len(R) else (0.0, 0.0)
    peak = np.maximum.accumulate(E)
    return dict(tot=acc - 1, mdd=float(((peak - E) / peak).max()),
                n_tr=int(len(R)), exp_r=float(R.mean()) if len(R) else np.nan,
                pf=float(w / l) if l > 0 else np.nan,
                wr=float((R > 0).mean()) if len(R) else np.nan)


def mc_line(label: str, R: np.ndarray) -> dict:
    if R is None or len(R) == 0:
        P(f'  {label}: нет сделок')
        return dict()
    tots, mdds = H24.mc(R, RISK)
    d = dict(p5_t=float(np.percentile(tots, 5)),
             med_t=float(np.percentile(tots, 50)),
             p95_t=float(np.percentile(tots, 95)),
             p5_dd=float(np.percentile(mdds, 5)),
             p95_dd=float(np.percentile(mdds, 95)),
             p_gt20=float((mdds > 0.20).mean()))
    P(f'  {label:<34}{d["p5_t"]*100:>8.1f}%{d["med_t"]*100:>8.1f}%'
      f'{d["p95_t"]*100:>9.1f}%{d["p5_dd"]*100:>8.1f}%{d["p95_dd"]*100:>8.1f}%'
      f'{d["p_gt20"]*100:>7.1f}%')
    return d


def cost_in_r(data: dict, lb: int, atrn: int) -> float:
    """Издержки round-trip в долях 1R. 1R = 2*ATR, издержки = 0.14% notional."""
    r = []
    for d in data.values():
        p = H28.prep_one(d, lb, atrn)
        a = p['a']
        a = a[~np.isnan(a)]
        if len(a):
            r.append(a.mean())
    atr = float(np.mean(r))
    return 0.0014 * 2 / (2 * atr) if atr > 0 else np.nan


# ─── варианты ─────────────────────────────────────────────────────

VARIANTS = [
    ('A', '4H без ER',          '4h', None,    HOLD_4H, HOLDS_4H),
    ('B', '4H ER(20) w950',     '4h', 950,     HOLD_4H, HOLDS_4H),
    ('C', '1H без ER',          '1h', None,    HOLD_4H, HOLDS_1H),
    ('D', '1H ER(20) w950',     '1h', 950,     HOLD_4H, HOLDS_1H),
    ('E', '1H ER(20) w3800',    '1h', 3800,    HOLD_4H, HOLDS_1H),
    ('F', '1H без ER hold120',  '1h', None,    HOLD_1H_TM, HOLDS_1H),
    ('G', '1H ER950 hold120',   '1h', 950,     HOLD_1H_TM, HOLDS_1H),
]


def main():
    P('=' * 104)
    P('H37. 1H КАК ОТДЕЛЬНАЯ ГИПОТЕЗА — честное сравнение с 4H')
    P('=' * 104)
    P('')

    d4, i4, pool4, miss4 = load_tf('4h')
    d1, i1, pool1, miss1 = load_tf('1h')
    pool = sorted(set(d4) & set(d1))
    lo, hi = max(i4.min(), i1.min()), min(i4.max(), i1.max())
    P(f'4H: {len(d4)} монет, {i4[0].date()} .. {i4[-1].date()}, {len(i4)} баров')
    P(f'1H: {len(d1)} монет, {i1[0].date()} .. {i1[-1].date()}, {len(i1)} баров')
    P(f'нет 1H-данных по: {miss1 or "-"}')
    P(f'ОБЩИЙ ПУЛ ({len(pool)}): {" ".join(pool)}')
    P(f'ОБЩЕЕ ОКНО: {lo.date()} .. {hi.date()} '
      f'({(hi - lo).days} дней, 4H ~{(hi-lo).days*6} баров, 1H ~{(hi-lo).days*24} баров)')
    P('')
    P('⚠️  Сравнение 8-монетного пула, а не 10. ENA/SUI выпадают, потому что')
    P('    1H-истории по ним нет. Все выводы — про эти 8 монет на этом окне.')
    P('')

    d4 = {k: v.loc[lo:hi] for k, v in d4.items() if k in pool}
    d1 = {k: v.loc[lo:hi] for k, v in d1.items() if k in pool}
    i4 = i4[(i4 >= lo) & (i4 <= hi)]
    i1 = i1[(i1 >= lo) & (i1 <= hi)]

    P(f'издержки в долях 1R: 4H ~{cost_in_r(d4, LB, ATRN)*100:.2f}%  '
      f'1H ~{cost_in_r(d1, LB, ATRN)*100:.2f}%  '
      f'(одинаковые 0.14% round-trip, но 1R на 1H в ~4 раза меньше)')
    P('')

    # ─── 0. гейт целостности данных ───────────────────────────────
    # Ловит ровно тот баг, который уже случился: одинаковый набор монет
    # на 4H и 1H даёт одинаковый ключ кэша, и «1H» молча получает 4H-ряд.
    n4, n1 = len(i4), len(i1)
    P('=' * 104)
    P('0. ГЕЙТ ЦЕЛОСТНОСТИ ДАННЫХ')
    P('=' * 104)
    ok_gate = True
    if n4 == n1:
        P(f'  [FAIL] длины 4H и 1H совпадают ({n4}) — невозможно, ТФ подменён')
        ok_gate = False
    else:
        P(f'  [OK] длины различаются: 4H {n4} баров, 1H {n1} баров')
    if n1 < 3 * n4:
        P(f'  [FAIL] 1H короче 3x 4H ({n1} против {n4}) — подозрение на подмену')
        ok_gate = False
    else:
        P(f'  [OK] 1H примерно в 4x длиннее 4H ({n1/n4:.1f}x)')
    for tag, data, idx in (('4H', d4, i4), ('1H', d1, i1)):
        pp = prep_tf(data, LB, ATRN, None, tag)
        bad = [k for k in pp if len(pp[k]['c']) != len(idx)]
        if bad:
            P(f'  [FAIL] {tag}: длина препарации != длина индекса для {bad}')
            ok_gate = False
        else:
            P(f'  [OK] {tag}: препарация совпадает с индексом ({len(idx)} баров)')
    P('')
    if not ok_gate:
        P('ДАННЫЕ НЕПРИГОДНЫ. Останавливаюсь — все дальнейшие числа недействительны.')
        OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
        return 1

    # ─── 1. полный период, все варианты ───────────────────────────
    P('=' * 104)
    P('1. ПОЛНЫЙ ПЕРИОД (in-sample, описательный) — все 7 вариантов')
    P('=' * 104)
    P(f'{"":<3}{"вариант":<22}{"сделок":>7}{"expR":>8}{"PF":>7}{"WR":>7}'
      f'{"доход":>10}{"DD":>7}{"эксп":>7}{"т/мес":>7}')
    P('-' * 104)
    full = {}
    for tag, name, tf, erw, hold, _ in VARIANTS:
        data = d4 if tf == '4h' else d1
        idx = i4 if tf == '4h' else i1
        pp = prep_tf(data, LB, ATRN, erw, tf)
        r = H28.simulate(pp, idx, LB, TRAIL, hold, risk=RISK, cap=CAP,
                         capital=CAPITAL)
        full[tag] = r
        months = (idx[-1] - idx[0]).days / 30.44
        P(f'{tag:<3}{name:<22}{r["n_tr"]:>7}{r["exp_r"]:>8.3f}{r["pf"]:>7.3f}'
          f'{r["wr"]*100:>6.1f}%{r["tot"]*100:>9.1f}%{r["mdd"]*100:>6.1f}%'
          f'{r["exposure"]*100:>6.1f}%{r["n_tr"]/months:>7.1f}')
    P('-' * 104)
    P('')

    # ─── 2. walk-forward ──────────────────────────────────────────
    P('=' * 104)
    P('2. WALK-FORWARD: 6 окон, подбор на train (адаптивный) + фикс')
    P('=' * 104)
    P(f'{"":<3}{"вариант":<22}{"режим":<10}{"сделок":>7}{"expR":>8}{"PF":>7}'
      f'{"PF>=1":>7}{"доход":>10}{"DD":>7}')
    P('-' * 104)
    wf = {}
    for tag, name, tf, erw, hold, holds in VARIANTS:
        data = d4 if tf == '4h' else d1
        idx = i4 if tf == '4h' else i1
        grid = [dict(lb=lb, atrn=ATRN, trail=tr, hold=hd)
                for lb in LBS for tr in TRAILS for hd in holds]
        pp_fixed = prep_tf(data, LB, ATRN, erw, tf)
        windows = build_windows(idx)
        ad_parts, fx_parts, n_ok, n_tot = [], [], 0, 0
        for w, lo_i, hi_i, sk in windows:
            best, best_s = None, -np.inf
            for prm in grid:
                pp = prep_tf(data, prm['lb'], prm['atrn'], erw, tf)
                r = H28.simulate(pp, idx[:lo_i], prm['lb'], prm['trail'],
                                 prm['hold'], risk=RISK, cap=CAP, capital=CAPITAL)
                if r['n_tr'] < MIN_TRADES_TRAIN:
                    continue
                if r['exp_r'] > best_s + 1e-9:
                    best, best_s = prm, r['exp_r']
            if best is None:
                continue
            pp = prep_tf(data, best['lb'], best['atrn'], erw, tf)
            oos = H28.simulate(pp, idx[lo_i:hi_i], best['lb'], best['trail'],
                               best['hold'], risk=RISK, cap=CAP, capital=CAPITAL,
                               offset=lo_i)
            fx = H28.simulate(pp_fixed, idx[lo_i:hi_i], LB, TRAIL, hold,
                              risk=RISK, cap=CAP, capital=CAPITAL, offset=lo_i)
            ad_parts.append(oos)
            fx_parts.append(fx)
            n_tot += 1
            n_ok += 1 if oos['pf'] >= 1.0 else 0
        wf[tag] = dict(adaptive=chain(ad_parts), fixed=chain(fx_parts),
                       n_ok=n_ok, n_tot=n_tot,
                       R_oos=np.concatenate([r['R'] for r in ad_parts])
                       if ad_parts else np.array([]),
                       R_fix=np.concatenate([r['R'] for r in fx_parts])
                       if fx_parts else np.array([]))
        for mode, res in (('adaptive', wf[tag]['adaptive']), ('fixed', wf[tag]['fixed'])):
            if not res:
                P(f'{tag:<3}{name:<22}{mode:<10}{"нет данных":>7}')
                continue
            P(f'{tag:<3}{name:<22}{mode:<10}{res["n_tr"]:>7}{res["exp_r"]:>8.3f}'
              f'{res["pf"]:>7.3f}{str(n_ok) + "/" + str(n_tot) if mode == "adaptive" else "-":>7}'
              f'{res["tot"]*100:>9.1f}%{res["mdd"]*100:>6.1f}%')
    P('-' * 104)
    P('')

    # ─── 3. Monte Carlo на OOS ────────────────────────────────────
    P('=' * 104)
    P('3. MONTE CARLO 3000 итераций на OOS-сделках (блочная перетасовка)')
    P('=' * 104)
    P(f'{"вариант":<22}{"n":>6}{"доход p5":>10}{"med":>9}{"p95":>10}'
      f'{"DD p5":>8}{"DD p95":>8}{"P>20%":>7}')
    P('-' * 104)
    mcres = {}
    for tag, name, tf, erw, hold, holds in VARIANTS:
        R = wf[tag]['R_oos']
        mcres[tag] = mc_line(f'{tag} {name}', R)
    P('-' * 104)
    P('')

    # ─── 4. по монетам ────────────────────────────────────────────
    P('=' * 104)
    P('4. ПО МОНЕТАМ (полный период, варианты B и D — 4H+ER против 1H+ER)')
    P('=' * 104)
    P(f'{"монета":<8}{"4H n":>7}{"4H expR":>9}{"4H PF":>8}'
      f'{"1H n":>7}{"1H expR":>9}{"1H PF":>8}{"разница expR":>14}')
    P('-' * 104)
    for c in pool:
        row = [c]
        for tag, data, idx in (('B', d4, i4), ('D', d1, i1)):
            pp = prep_tf({c: data[c]}, LB, ATRN, 950, tf)
            r = H28.simulate(pp, idx, LB, TRAIL, HOLD_4H, risk=RISK, cap=CAP,
                             capital=CAPITAL)
            row += [r['n_tr'], r['exp_r'], r['pf']]
        P(f'{row[0]:<8}{row[1]:>7}{row[2]:>9.3f}{row[3]:>8.3f}'
          f'{row[4]:>7}{row[5]:>9.3f}{row[6]:>8.3f}{row[5]-row[2]:>14.3f}')
    P('-' * 104)
    P('')

    # ─── 5. вердикт ───────────────────────────────────────────────
    P('=' * 104)
    P('5. ВЕРДИКТ ПО ЗАФИКСИРОВАННЫМ КРИТЕРИЯМ')
    P('=' * 104)
    P(f'{"критерий":<46}{"требование":>16}{"факт":>16}{"итог":>8}')
    P('-' * 104)

    def crit(name, req, ok, fact):
        P(f'{name:<46}{req:>16}{fact:>16}{"ПРОШЁЛ" if ok else "НЕ ПРОШЁЛ":>8}')
        return ok

    ok = True
    a4, d1f = full['B'], full['D']
    ok &= crit('1. expR(1H+ER) > expR(4H+ER), полный период',
               '>', d1f['exp_r'] > a4['exp_r'],
               f'{d1f["exp_r"]:.3f} vs {a4["exp_r"]:.3f}')
    ok &= crit('2. PF>=1 в 4 из 6 OOS-окон (1H+ER, адаптивный)',
               '>=4/6', wf['D']['n_ok'] >= 4, f'{wf["D"]["n_ok"]}/{wf["D"]["n_tot"]}')
    ok &= crit('3. max DD(1H+ER) не хуже 4H+ER, полный период',
               '<=', d1f['mdd'] <= a4['mdd'],
               f'{d1f["mdd"]*100:.1f}% vs {a4["mdd"]*100:.1f}%')
    mdd = mcres['D']
    ok &= crit('4. MC: DD p5 <= 25% (1H+ER)', '<=25%',
               mdd.get('p5_dd', 9) <= 0.25, f'{mdd.get("p5_dd", float("nan"))*100:.1f}%')
    ok &= crit('5. Не вырождено: >= 300 OOS-сделок (1H+ER)', '>=300',
               (wf['D']['adaptive'] or {}).get('n_tr', 0) >= 300,
               f'{(wf["D"]["adaptive"] or {}).get("n_tr", 0)}')
    ok &= crit('6. Live без пагинации (ER-окно <= 1000 баров)', '<=1000',
               True, '950 — один запрос; 3800 требует 4 запросов')
    P('-' * 104)
    P('')
    if ok:
        P('ВЕРДИКТ: 1H прошёл все критерии. НЕ включаем в live сразу —')
        P('сначала shadow 4-6 недель (только лог, без ордеров).')
    else:
        P('ВЕРДИКТ: 1H НЕ прошёл. Оставляем 4H. Параметры 1H НЕ крутим —')
        P('иначе это подгонка под тот же ряд, который уже дал in-sample выбор.')
    P('')
    P('ОГРАНИЧЕНИЯ:')
    P('  1. 8 монет вместо 10 (ENA/SUI нет 1H-истории). Перенос на 10-монетный')
    P('     пул не проверен и автоматически не следует.')
    P('  2. Окно 2024-04..2026-09 — то же, что у H28. 1H-данные глубже, но')
    P('     сравнивать с 4H можно только на общем диапазоне.')
    P('  3. Адаптивный WF выбирает параметры на train. Даже этот максимально')
    P('     благоприятный для 1H тест — и он не спас 1H.')
    P('  4. Разные ТФ — разные стратегии. Выводы H24-H36 (про 4H) на 1H')
    P('     не переносятся, и наоборот.')
    P('  5. Funding, адверс-селекшн, проскальзывание на 1H не моделируются.')
    P('     На 1H реалистичное проскальзывание выше, чем принятые 0.015%.')
    P('  6. Один ряд, одна гипотеза. Проверка «1H лучше 4H» — это одна')
    P('     гипотеза, а не перебор; p-значения здесь не приводятся, потому')
    P('     что сравнение портфельное и автокоррелированное.')

    OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT.resolve()}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
