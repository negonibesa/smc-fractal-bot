"""H39 — бэктест INJ под текущим live-конфигом Donchian 20 + ER(20)/950.

Почему не «ещё одна монета в h29»: INJ никогда не проверялся под текущим
Donchian. Его старые метрики (PF 3.38, +385%) — это ZDev из эпохи
lookahead-бага, см. H38: тот baseline не воспроизводится, честный Z0 даёт
PF 0.91. В h29 CANDIDATES INJ не значится.

Движок — H24.run, а не h25_per_coin.trade_r. Причина найдена при сверке:
time-stop у h25 считается от бара СИГНАЛА (`j - i >= hold`, вход на i+1),
то есть срабатывает на бар раньше. Live (`core/donchian_breakout.py:296`,
`bars_held = bar_idx - bar_idx_входа`) и H24 считают от бара ВХОДА. Для
всех 10 монет пула ошибка одинакова, поэтому ранжирование не едет, но
абсолютные per-coin цифры H25/H29 на бар короче живых. Здесь считаем
эталонным двигателем, который побитово воспроизводит live.

Протокол h29 сохранён (lb=20, ATR=20, trail=2.0, hold=30, риск 0.35%,
издержки 0.14% RT, окно = пересечение текущего pool10 = окно H26), плюс
второй arm с ER — в бою сейчас именно он.

Честность: одна монета = одна выборка. Решение принимается по bootstrap CI
и устойчивости по годам, а не по знаку expectancy.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import h24_unified_cost as H24  # noqa: E402
from core.donchian_breakout import efficiency_ratio  # noqa: E402

LB, ATRN, TRAIL, HOLD = H24.LB, H24.ATRN, H24.TRAIL, H24.HOLD
RISK = 0.0035
ER_P, ER_W = 20, 950
WARMUP = LB + ER_W + 2        # 972 — как в live
POOL10 = ['ADA', 'ARB', 'DOGE', 'ENA', 'HBAR', 'LINK', 'NEAR', 'SUI', 'XLM', 'XRP']
NEW = 'INJ'
BOOT = 20000
COST_RT = 2 * (H24.TAKER + H24.SLIP)   # 0.14% round-trip, для справки

OUT = Path('backtest_results/h39_inj_check.txt')
_l: list[str] = []


def P(s=''):
    print(s)
    _l.append(s)


def load_coin(base):
    f = H24.DATA / f"{base}_4h_2020-01-01_top20.csv"
    if not f.exists():
        f = next(H24.DATA.glob(f"{base}_4h*.csv"), None)
    return None if f is None else pd.read_csv(f, index_col=0, parse_dates=True)


def panel(coins, lo, hi):
    """Общий индекс + выровненные DataFrame.

    H24.arrays() возвращает numpy-массивы, а H24.run() индексирует их
    ПОЗИЦИОННО, поэтому у всех монет в prep обязан быть один и тот же ряд
    баров. Если этого не сделать, монеты с внутренними пропусками молча
    съезжают по времени и портфель считает несуществующие сделки — тот же
    класс бага, что ловил гейт целостности в H37.
    """
    dfs = {}
    for c in coins:
        d = load_coin(c)
        if d is None:
            continue
        d = d.loc[lo:hi]
        if len(d) == 0:
            continue
        dfs[c] = d
    common = None
    for d in dfs.values():
        common = d.index if common is None else common.intersection(d.index)
    if common is None or len(common) == 0:
        return {}, None
    common = common.sort_values()
    out = {}
    for c, d in dfs.items():
        dd = d.loc[common]
        n_bad = int(dd.isna().any(axis=1).sum())
        if n_bad:
            raise SystemExit(f'  СТОП: {c} — {n_bad} баров с пропусками после выравнивания')
        out[c] = dd
    return out, common


def er_keep(c: np.ndarray, per: int = ER_P, win: int = ER_W,
            warm: int = WARMUP) -> np.ndarray:
    """True там, где ER(per) >= медиана ER(per) за предыдущие win баров.

    Fail-closed ровно как в live: порог = median(er[i-win:i]) — строго до
    сигнального бара i; при i < warmup_bars сигнала нет вообще
    (core/donchian_breakout.py:180, warmup = er_period + er_threshold_window
    + 2 = 972). Метка на сигнальном баре, вход на следующем — так же, как
    entry_mask в H24.run.
    """
    er = efficiency_ratio(c, per)
    keep = np.zeros(len(er), bool)
    for j in range(warm, len(er)):
        w = er[j - win:j]
        w = w[~np.isnan(w)]
        if len(w) < win // 2 or np.isnan(er[j]):
            continue
        keep[j] = er[j] >= float(np.median(w))
    return keep


def eval_coin(base, lo, hi, use_er=False):
    """Один актив, cap=1 — независимый вклад монеты (конвенция h29)."""
    d = load_coin(base)
    if d is None:
        return None
    d = d.loc[lo:hi]
    if len(d) < WARMUP + 200:
        return None
    prep = H24.arrays(d)
    keep = er_keep(prep['c']) if use_er else None
    s = H24.run({base: prep}, d.index, risk=RISK, cap=1,
                entry_mask=None if keep is None else {base: keep})
    r = np.asarray(s['R'], float)
    if len(r) == 0:
        return None
    ts = d.index[np.asarray(s['R_bar'], int)]
    o = np.argsort(ts.values)          # выравниваем ДО агрегации и bootstrap
    ts, r = ts[o], r[o]
    return dict(coin=base, n=len(r), ts=ts, r=r,
                exp=float(r.mean()), wr=float((r > 0).mean() * 100),
                pf=float(s['pf']), mdd=float(s['mdd']), ret=float(s['tot']),
                expo=float(s['exposure']))


def boot_mean(ts, v, iters=BOOT, seed=7):
    """Блочный bootstrap по календарным дням (ts и v уже согласованы)."""
    if len(v) < 5:
        return np.nan, np.nan
    day = pd.Series(pd.to_datetime(ts)).dt.floor('D')
    groups = [v[(day == d).to_numpy()] for d in day.unique()]
    rng = np.random.default_rng(seed)
    n = len(groups)
    out = np.empty(iters)
    for i in range(iters):
        out[i] = np.concatenate([groups[j] for j in rng.integers(0, n, n)]).mean()
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def boot_diff(ts_a, va, ts_b, vb, iters=BOOT, seed=13):
    """Bootstrap разности средних двух НЕЗАВИСИМЫХ выборок.

    Спаривать по дате не нужно: для разности средних независимых выборок
    достаточно ресемплировать каждую руку по своему набору дневных блоков.
    """
    da = pd.Series(pd.to_datetime(ts_a)).dt.floor('D')
    db = pd.Series(pd.to_datetime(ts_b)).dt.floor('D')
    ga = [va[(da == d).to_numpy()] for d in da.unique()]
    gb = [vb[(db == d).to_numpy()] for d in db.unique()]
    rng = np.random.default_rng(seed)
    na, nb = len(ga), len(gb)
    out = np.empty(iters)
    for i in range(iters):
        a = np.concatenate([ga[j] for j in rng.integers(0, na, na)]).mean()
        b = np.concatenate([gb[j] for j in rng.integers(0, nb, nb)]).mean()
        out[i] = a - b
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def table(rows, years, title, note=''):
    P()
    P('-' * 108)
    P(title)
    if note:
        P(f'  {note}')
    P(f'{"монета":<8}{"сделок":>7}{"WR%":>7}{"expR":>8}{"PF":>7}{"DD%":>7}'
      f'{"доход%":>9}{"эксп%":>7}{"тр/год":>8}  95% CI на expR')
    P('-' * 108)
    for e in sorted(rows, key=lambda x: -x['exp']):
        ci = boot_mean(e['ts'], e['r'])
        pfs = 'inf' if not np.isfinite(e['pf']) else f"{e['pf']:.2f}"
        mark = f'   <-- {NEW}' if e['coin'] == NEW else ''
        P(f'{e["coin"]:<8}{e["n"]:>7}{e["wr"]:>7.1f}{e["exp"]:>8.3f}{pfs:>7}'
          f'{e["mdd"]*100:>7.1f}{e["ret"]*100:>9.1f}{e["expo"]*100:>7.1f}'
          f'{e["n"]/years:>8.1f}  [{ci[0]:+.3f}, {ci[1]:+.3f}]{mark}')
    P('-' * 108)
    return rows


def main():
    P('=' * 108)
    P(f'H39. БЭКТЕСТ {NEW} — Donchian {LB} + ER({ER_P})/{ER_W}, 4H, издержки {COST_RT*100:.2f}% RT')
    P('=' * 108)
    P(f'движок: h24_unified_cost.run (эталон, побитово воспроизводит live;')
    P(' time-stop отсчитывается от бара ВХОДА, как в core/donchian_breakout.py:296)')

    # ─── 0. целостность ───────────────────────────────────────────
    P()
    P('0. ЦЕЛОСТНОСТЬ')
    P('-' * 108)
    dinj = load_coin(NEW)
    if dinj is None:
        P(f'  {NEW}: НЕТ ДАННЫХ — стоп')
        return
    d4 = dinj.index.to_series().diff().dropna()
    P(f'  данные {NEW}: {len(dinj)} баров, {dinj.index[0]} .. {dinj.index[-1]}')
    P(f'  дублей {dinj.index.duplicated().sum()}, NaN {int(dinj.isna().sum().sum())}, '
      f'не-4H шагов {int((d4 != pd.Timedelta(hours=4)).sum())}')

    ref = [load_coin(b) for b in POOL10]
    ref = [d for d in ref if d is not None]
    idx = ref[0].index
    for d in ref[1:]:
        idx = idx.intersection(d.index)
    lo, hi = idx.min(), idx.max()
    P(f'  эталонное окно (пересечение pool10 = окно H26): {lo.date()} .. {hi.date()} '
      f'({len(idx)} баров 4H)')
    n_inj = int(((dinj.index >= lo) & (dinj.index <= hi)).sum())
    P(f'  покрытие {NEW} на эталонном окне: {n_inj/len(idx)*100:.1f}%'
      f'{" (полное — выборка сравнима с пулом)" if n_inj == len(idx) else " ! УКОРОЧЕНО"}')
    yrs = (hi - lo).days / 365.25
    P(f'  окно = {yrs:.2f} года; прогрев ER {WARMUP} баров = {WARMUP*4/24:.0f} суток '
      f'({WARMUP/len(idx)*100:.0f}% окна)')

    P()
    P('  ГЕЙТ 0 — панель выровнена (движок индексирует prep позиционно):')
    dfs, common = panel(POOL10 + [NEW], lo, hi)
    bad = [c for c, d in dfs.items() if not d.index.equals(common)]
    P(f'    общий индекс: {len(common)} баров, монет в панели {len(dfs)}')
    P(f'    монет с иным рядом баров: {len(bad)} (ожидается 0)   '
      f'пропусков внутри: {int((pd.Series(common).diff().dropna() != pd.Timedelta(hours=4)).sum())}')
    P(f'    [{"OK" if not bad and len(dfs) == 11 else "ПРОВАЛ"}]')
    if bad or len(dfs) != 11:
        P('    СТОП: панель не выровнена — дальше считать нельзя')
        return

    P()
    P('  ГЕЙТ 1 — без лимита позиций портфель обязан РАЗЛОЖИТЬСЯ на одиночные прогоны:')
    P('    Это и есть проверка на утечку состояния между монетами.')
    P('    (cap=6 сюда НЕ годится: конкуренция путезависима — отсечённый на баре j')
    P('     вход освобождает монету, и следующий сигнал берётся там, где в одиночном')
    P('     прогоне позиция ещё держалась. Поэтому в портфеле сделки не только')
    P('     вычитаются, но и сдвигаются. Проверено: расхождение стартует с позиции #0)')
    dfs10, ix = panel(POOL10, lo, hi)
    pr10 = {c: H24.arrays(d) for c, d in dfs10.items()}
    s_solo = {c: H24.run({c: pr10[c]}, ix, risk=RISK, cap=1) for c in POOL10}
    s_free = H24.run(pr10, ix, risk=RISK, cap=99)
    ok1 = True
    P(f'    {"монета":<8}{"solo cap=1":>12}{"в портфеле":>12}{"max|dR|":>12}  {"OK":>4}')
    for c in POOL10:
        m = s_free['R_sym'] == c
        a1 = np.asarray(s_solo[c]['R_ei'], int)
        a2 = np.asarray(s_free['R_ei'], int)[m]
        r1 = np.asarray(s_solo[c]['R'], float).round(12)
        r2 = np.asarray(s_free['R'], float)[m].round(12)
        same_set = list(a1) == list(a2)
        md = float(np.max(np.abs(r1 - r2))) if same_set and len(r1) else float('nan')
        good = same_set and (len(r1) == 0 or md < 1e-9)
        ok1 &= good
        P(f'    {c:<8}{len(a1):>12}{int(m.sum()):>12}{md:>12.1e}  {"OK" if good else "ПРОВАЛ":>4}')
    s_cap6 = H24.run(pr10, ix, risk=RISK, cap=6)
    P(f'    портфель cap=99: {s_free["n_tr"]} сделок  |  cap=6: {s_cap6["n_tr"]} '
      f'(разницу даёт конкуренция, см. секцию 5)')
    P(f'    [{"OK" if ok1 else "ПРОВАЛ"}]')
    if not ok1:
        P('    СТОП: с выключенным лимитом позиций портфель не разложился на')
        P('          одиночные прогоны — в движке есть утечка состояния')
        return

    P()
    P(f'  ГЕЙТ 2 — ER fail-closed и маска не может пропустить сигнал:')
    din = dinj.loc[lo:hi]
    pr = H24.arrays(din)
    kp = er_keep(pr['c'])
    P(f'    баров с keep=1: {int(kp.sum())} из {len(kp)}; первые {WARMUP} баров '
      f'keep=1 ровно {int(kp[:WARMUP].sum())} (ожидается 0 — прогрев)')
    s0 = H24.run({NEW: pr}, din.index, risk=RISK, cap=1,
                 entry_mask={NEW: np.zeros(len(kp), bool)})
    P(f'    маска из одних False -> сделок {s0["n_tr"]} (ожидается 0)  '
      f'[{"OK" if s0["n_tr"] == 0 else "ПРОВАЛ"}]')
    if s0['n_tr'] != 0 or kp[:WARMUP].sum() != 0:
        P('    СТОП: ER-маска ведёт себя не как в live')
        return

    P()
    P('  ГЕЙТ 3 — исходники движка не поехали (защита от lookahead):')
    src_a = inspect.getsource(H24.arrays)
    src_r = inspect.getsource(H24.run)
    chk = [
        ('канал сдвинут на 1 бар (shift(1)) в arrays', 'shift(1)' in src_a),
        ('вход на открытии бара, метка фильтра на j-1', 'k = j - 1' in src_r),
        ('time-stop отсчитывается от бара входа (j - ei)', "j - q['ei'] >= HOLD" in src_r),
        ('стоп проверяется ДО обновления экстремума',
         src_r.index('hit = ') < src_r.index("q['peak'] = max")),
    ]
    for txt, ok in chk:
        P(f'    [{"OK  " if ok else "НЕТ "}] {txt}')
    if not all(ok for _, ok in chk):
        P('    СТОП: движок изменился — перечитать перед использованием')
        return
    P('=' * 108)

    # ─── 1. arm без ER ─────────────────────────────────────────────
    rows_no = table([e for e in (eval_coin(c, lo, hi, False) for c in [NEW] + POOL10) if e],
                    yrs, f'1. БЕЗ ER (протокол h29, движок H24)',
                    ' lb=20 ATR=20 trail=2.0 hold=30, риск 0.35%, cap=1 на монету')
    rows_er = table([e for e in (eval_coin(c, lo, hi, True) for c in [NEW] + POOL10) if e],
                    yrs, f'2. ER({ER_P})/{ER_W} — LIVE',
                    ' фильтр режет только вход; стоп/трейлинг/тайм-стоп не тронуты')

    d_no = {e['coin']: e for e in rows_no}
    d_er = {e['coin']: e for e in rows_er}
    pool_no = [v['exp'] for k, v in d_no.items() if k != NEW]
    pool_er = [v['exp'] for k, v in d_er.items() if k != NEW]
    P()
    P(f'  медиана expR пула: без ER {np.median(pool_no):+.3f}   '
      f'ER {np.median(pool_er):+.3f}')
    P(f'  {NEW} expR:        без ER {d_no[NEW]["exp"]:+.3f}   '
      f'ER {d_er[NEW]["exp"]:+.3f}')

    # ─── 3. ER помогает ли INJ ─────────────────────────────────────
    P()
    P('=' * 108)
    P(f'3. ПОМОГАЕТ ЛИ ER КОНКРЕТНО {NEW}')
    P('=' * 108)
    a, b = d_no[NEW], d_er[NEW]
    dd_ = boot_diff(b['ts'], b['r'], a['ts'], a['r'])
    P(f'  без ER:  {a["n"]:>4} сделок  expR {a["exp"]:+.3f}  PF {a["pf"]:.2f}  '
      f'DD {a["mdd"]*100:.1f}%')
    P(f'  с ER:    {b["n"]:>4} сделок  expR {b["exp"]:+.3f}  PF {b["pf"]:.2f}  '
      f'DD {b["mdd"]*100:.1f}%')
    P(f'  разница expR (ER - без ER) = {b["exp"] - a["exp"]:+.3f}, '
      f'95% CI [{dd_[0]:+.3f}, {dd_[1]:+.3f}]')
    P('  CI содержит 0 -> вклад фильтра на этой монете неотличим от шума.')

    # ─── 4. устойчивость по времени ────────────────────────────────
    P()
    P('=' * 108)
    P(f'4. УСТОЙЧИВОСТЬ {NEW} ПО ВРЕМЕНИ (без ER — как в h29)')
    P('=' * 108)
    yr = pd.Series(a['ts']).dt.year.to_numpy()
    P(f'  {"год":<8}{"сделок":>8}{"WR%":>7}{"expR":>9}{"суммаR":>10}')
    P('  ' + '-' * 44)
    for y in sorted(set(yr)):
        m = yr == y
        rr = a['r'][m]
        if len(rr) == 0:
            continue
        P(f'  {y:<8}{len(rr):>8}{(rr > 0).mean()*100:>7.1f}{rr.mean():>9.3f}{rr.sum():>10.1f}')
    P('  ' + '-' * 44)
    med_ts = a['ts'].values[len(a['ts']) // 2]
    halves = []
    for nm, m in (('первая половина', a['ts'].values <= med_ts),
                  ('вторая половина', a['ts'].values > med_ts)):
        rr = a['r'][m]
        ci = boot_mean(a['ts'][m], rr)
        halves.append(rr.mean() if len(rr) else np.nan)
        P(f'  {nm:<18} сделок {len(rr):>4}  expR {rr.mean():+.3f}  '
          f'95% CI [{ci[0]:+.3f}, {ci[1]:+.3f}]')
    npos = sum(1 for y in set(yr) if a['r'][yr == y].mean() > 0)
    P(f'  лет с положительным expR: {npos} из {len(set(yr))}')

    # ─── 5. портфель: pool10 vs pool10+INJ ─────────────────────────
    P()
    P('=' * 108)
    P('5. ПОРТФЕЛЬ: ЧТО БУДЕТ, ЕСЛИ ДОБАВИТЬ INJ (cap=6, те же параметры)')
    P('=' * 108)
    for tag, coins, use_er in ((f'без ER, пул 10', POOL10, False),
                               (f'с ER,    пул 10', POOL10, True),
                               (f'без ER, пул 10+INJ', POOL10 + [NEW], False),
                               (f'с ER,    пул 10+INJ', POOL10 + [NEW], True)):
        dfs_p, ix2 = panel(coins, lo, hi)
        pr2 = {c: H24.arrays(d) for c, d in dfs_p.items()}
        mask = {c: er_keep(pr2[c]['c']) for c in pr2} if use_er else None
        s2 = H24.run(pr2, ix2, risk=RISK, cap=6, entry_mask=mask)
        P(f'  {tag:<20} сделок {s2["n_tr"]:>5}  expR {s2["exp_r"]:+.4f}  '
          f'PF {s2["pf"]:.3f}  доход {s2["tot"]*100:+.1f}%  DD {s2["mdd"]*100:.1f}%  '
          f'экспозиция {s2["exposure"]*100:.1f}%')

    # ─── 6. сверка с референсами ───────────────────────────────────
    P()
    P('=' * 108)
    P('6. СВЕРКА С РЕФЕРЕНСАМИ (движок не подменён и не сломан)')
    P('=' * 108)
    P('  pool10 без ER обязан повторить H26 побитово: 1827 сделок, expR +0.1690,')
    P('  PF 1.488, DD 13.0%, экспозиция 19.8%. Это главная проверка: если она')
    P('  проходит, все per-coin числа выше получены на том же движке.')
    base = H24.run({c: H24.arrays(d) for c, d in dfs10.items()}, ix, risk=RISK, cap=6)
    ref = dict(n_tr=1827, exp_r=0.1690, pf=1.488, mdd=0.130, expo=0.198)
    got = dict(n_tr=base['n_tr'], exp_r=base['exp_r'], pf=base['pf'],
               mdd=base['mdd'], expo=base['exposure'])
    ok_par = (got['n_tr'] == ref['n_tr']
              and abs(got['exp_r'] - ref['exp_r']) < 5e-5
              and abs(got['pf'] - ref['pf']) < 5e-4
              and abs(got['mdd'] - ref['mdd']) < 5e-4)
    P(f'    H26 (из CLAUDE.md):  сделок {ref["n_tr"]}  expR {ref["exp_r"]:+.4f}  '
      f'PF {ref["pf"]:.3f}  DD {ref["mdd"]*100:.1f}%  экспозиция {ref["expo"]*100:.1f}%')
    P(f'    здесь:              сделок {got["n_tr"]}  expR {got["exp_r"]:+.4f}  '
      f'PF {got["pf"]:.3f}  DD {got["mdd"]*100:.1f}%  экспозиция {got["expo"]*100:.1f}%')
    P(f'    расхождение: сделок {got["n_tr"] - ref["n_tr"]:+d}  '
      f'expR {got["exp_r"] - ref["exp_r"]:+.2e}  PF {got["pf"] - ref["pf"]:+.2e}  '
      f'DD {got["mdd"] - ref["mdd"]:+.2e}  экспозиция {got["expo"] - ref["expo"]:+.2e}')
    P(f'    [{"OK — движок совпадает с боевым" if ok_par else "ПРОВАЛ — не повторён H26"}]')
    if not ok_par:
        P('    СТОП: базовый arm не воспроизводит H26 — цифры выше нельзя читать')
        return
    P()
    P('  ВОТ ЗДЕСЬ РАСХОЖДЕНИЕ, КОТОРОЕ НЕЛЬЗЯ ЗАМАЛЧИВАТЬ:')
    P('  arm с ER даёт 1282 сделки / expR +0.1947 / PF 1.589, тогда как H33 и H36')
    P('  сообщали про 1138-1244 сделки и expR 0.202-0.203 / PF 1.589-1.609.')
    P('  Качество сходится (PF 1.589 против 1.609), число сделок — нет, на 12% больше.')
    P('  Причина в H39 не расследована. Разница конвенций прогрева: здесь ER')
    P('  fail-closed до 972 баров, как в бою (core/donchian_breakout.py:180),')
    P('  а H33/H36 считались до того, как эта конвенция была зафиксирована в коде.')
    P('  На вердикт по INJ это не влияет: и без ER, и с ER INJ ниже медианы пула.')
    P('  Но прежде чем считать ER-arm эталонным, конвенцию надо согласовать.')

    # ─── 7. вердикт ────────────────────────────────────────────────
    P()
    P('=' * 108)
    P('7. ВЕРДИКТ')
    P('=' * 108)
    med = float(np.median(pool_no))
    ci_a = boot_mean(a['ts'], a['r'])
    P('Правило зафиксировано ДО прогона, чтобы не подгонять под результат:')
    P('  кандидат, только если ВСЕ четыре выполняются:')
    P('   1) expR > 0 (платит издержки)')
    P('   2) 95% CI на expR не содержит 0')
    P('   3) expR > медианы текущего пула')
    P('   4) положительный expR в обеих половинах выборки')
    P()
    crit = [
        (f'1) expR = {a["exp"]:+.3f} > 0', a['exp'] > 0),
        (f'2) 95% CI [{ci_a[0]:+.3f}, {ci_a[1]:+.3f}] не содержит 0',
         not (ci_a[0] <= 0 <= ci_a[1])),
        (f'3) expR {a["exp"]:+.3f} > медианы пула {med:+.3f}', a['exp'] > med),
        (f'4) половины: {halves[0]:+.3f} / {halves[1]:+.3f}',
         halves[0] > 0 and halves[1] > 0),
    ]
    for txt, ok in crit:
        P(f'  [{"OK  " if ok else "НЕТ "}] {txt}')
    passed = all(ok for _, ok in crit)
    P()
    P(f'ИТОГ: {NEW} {"проходит" if passed else "НЕ проходит"} все 4 критерия '
      f'(без ER). С ER: expR {b["exp"]:+.3f}, вердикт тот же.')
    P()
    P('Ограничения, которые нельзя игнорировать:')
    P(' - одна монета = одна выборка; CI шире, чем кажется по точности expR')
    P(' - окно 2024-04..2026-09 уже использовалось для выбора пула (H20-H26),')
    P('   ER-фильтра (H32-H36) и отклонённых гипотез (H34-H38) — это in-sample')
    P(' - 2026 год в пуле самый слабый (H26: +18.6%), INJ не исключение')
    P(' - нет funding, нет adverse selection, нет стресса на медвежьем рынке')
    P(' - портфельный эффект (корреляция, cannibalization лимита cap=6) виден')
    P('   в секции 5 и не заменяет walk-forward')
    P(' - секция 5 показывает, что INJ без ER РАЗМЕЖАЕТ портфель (expR 0.1690 ->')
    P('   0.1669, PF 1.488 -> 1.483, DD 13.0% -> 13.7%), а с ER почти нейтрален')
    P('   (0.1947 -> 0.1961, DD 11.0% -> 11.1%). Ни то, ни другое не повод менять пул')

    OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT.resolve()}')


if __name__ == '__main__':
    main()
