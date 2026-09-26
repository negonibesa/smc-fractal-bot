"""H28 — walk-forward + Monte Carlo для Donchian pool10.

Зачем: H17–H26 отбирали пул и параметры на одних и тех же данных
2024-04..2026-09. Итоговые метрики — описание уже случившегося. WF
проверяет, выбираются ли те же параметры на невиданном, и что стратегия
делает на данных, которые не видела.

Методика
--------
1. Верификация симулятора: на дефолтах (lb=20, trail=2.0, hold=30, risk
   0.35%) обязано совпасть с H26: 1827 сделок, expR 0.1690, PF 1.488,
   итог 158.5%, DD 13.04%. Если не совпало — все дальнейшие числа мусор.
2. Раскладка: первые 6 месяцев — только train, дальше 6 тестовых окон
   по 4 месяца с расширяющимся train. Итого 24 месяца из 30 — 80%
   времени вне обучения.
3. На каждом train перебирается сетка, выбирается лучший expectancy;
   на test применяются выбранные параметры (adaptive) и одновременно
   фиксированный базовый lb=20/trail=2.0/hold=30 (baseline). Разница
   показывает, переносится ли подбор параметров.
4. Критерий приёмки от заказчика: >= 4 из 6 окон с PF >= 1.
5. Monte Carlo 3000 итераций блочной перетасовки на суммарной
   последовательности OOS-сделок.

Чего здесь нет: переотбор пула внутри каждого окна (pool выбран в H25
in-sample). Это отдельный источник переобучения, см. отчёт.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import h24_unified_cost as H24  # noqa: E402

OUT = Path('backtest_results/h28_walkforward.txt')
_l: list[str] = []

TAKER, SLIP = H24.TAKER, H24.SLIP
CAPITAL, LEV, MARGIN_BUF = H24.CAPITAL, H24.LEV, H24.MARGIN_BUF
CAP = H24.CAP

BASE = dict(lb=20, atrn=20, trail=2.0, hold=30)
RISK = 0.0035

LBS = (10, 15, 20, 30, 40)
TRAILS = (1.5, 2.0, 2.5, 3.0)
HOLDS = (15, 20, 30, 40)
GRID = [dict(lb=lb, atrn=20, trail=tr, hold=hd)
        for lb in LBS for tr in TRAILS for hd in HOLDS]

TRAIN_MONTHS = 6
TEST_MONTHS = 4
N_WINDOWS = 6
MIN_TRADES_TRAIN = 30


def P(s=''):
    print(s)
    _l.append(s)


# ─── симулятор (параметризованная копия H24.run) ────────────────────

def prep_one(d: pd.DataFrame, lb: int, atrn: int) -> dict:
    o = d['open'].to_numpy(float)
    c = d['close'].to_numpy(float)
    h = d['high'].to_numpy(float)
    l = d['low'].to_numpy(float)
    a = pd.Series(H24.true_range(o, h, l, c)).rolling(atrn).mean().to_numpy()
    up = pd.Series(h).rolling(lb).max().shift(1).to_numpy()
    dn = pd.Series(l).rolling(lb).min().shift(1).to_numpy()
    sig = np.zeros(len(d))
    sig[c > up] = 1.0
    sig[c < dn] = -1.0
    sig[np.isnan(up)] = 0.0
    return dict(o=o, c=c, h=h, l=l, a=a, sig=sig)


def simulate(prep: dict, idx, lb: int, trail: float, hold: int,
             risk: float = RISK, cap: int = CAP,
             capital: float = CAPITAL, offset: int = 0) -> dict:
    """offset — с какого бара полного ряда начинается окно.

    Критично: индикаторы (канал, ATR) считаются на ПОЛНОМ ряду, а
    симуляция идёт по окну. Если индексировать prep с нуля, каждое окно
    получит бары с начала датасета — и все окна дадут одинаковый результат.
    offset решает это, сохраняя прогрев: канал на первом бару окна смотрит
    на бары ДО окна, как и в живом боре, где история всегда есть.
    """
    n = len(idx)
    if n == 0:
        return dict(tot=0.0, mdd=0.0, n_tr=0, exp_r=np.nan, pf=np.nan,
                    wr=np.nan, exposure=0.0, eq=np.zeros(0), dd=np.zeros(0),
                    R=np.array([]), trades=[])
    coins = sorted(prep)
    cash = capital
    openp: dict = {}
    pend: dict = {}
    eq = np.empty(n)
    used = np.zeros(n, bool)
    trades = []          # (coin, R, entry_bar, exit_bar)

    for j in range(n):
        k = offset + j
        # --- входы на открытии бара
        for co, s in list(pend.items()):
            p = prep[co]
            ae = p['a'][k]
            if np.isnan(ae) or ae <= 0:
                continue
            rpu = trail * ae
            if rpu <= 0:
                continue
            entry = p['o'][k] * (1 + SLIP)
            cur = cash + sum(q['qty'] * (prep[qk]['c'][k] - q['entry']) * q['side']
                             for qk, q in openp.items())
            qty = (cur * risk) / rpu
            notional = qty * entry
            marg = sum(q['qty'] * prep[qk]['c'][k] for qk, q in openp.items())
            if cap and len(openp) >= cap:
                continue
            if marg + notional > cur * LEV * MARGIN_BUF:
                continue
            cash -= qty * entry * TAKER
            openp[co] = dict(side=s, entry=entry, qty=qty,
                             stop=entry - s * trail * ae, peak=entry, ei=k)
            used[j] = True
        pend.clear()

        # --- стоп ДО обновления экстремума, стоп ДО time-stop
        for co, q in list(openp.items()):
            p = prep[co]
            hit = (p['l'][k] <= q['stop']) if q['side'] == 1 else (p['h'][k] >= q['stop'])
            timed = (k - q['ei'] >= hold)
            if not (hit or timed):
                if q['side'] == 1:
                    q['peak'] = max(q['peak'], p['h'][k])
                    q['stop'] = max(q['stop'], q['peak'] - trail * p['a'][q['ei']])
                else:
                    q['peak'] = min(q['peak'], p['l'][k])
                    q['stop'] = min(q['stop'], q['peak'] + trail * p['a'][q['ei']])
                continue
            raw = q['stop'] if hit else p['c'][k]
            px = raw * (1 - SLIP)
            pnl = (q['qty'] * (px - q['entry']) * q['side']) - q['qty'] * px * TAKER
            cash += pnl
            rd = q['qty'] * trail * p['a'][q['ei']]
            trades.append((co, pnl / rd if rd > 0 else 0.0, q['ei'], k))
            del openp[co]

        e = cash
        for co, q in openp.items():
            e += q['qty'] * (prep[co]['c'][k] - q['entry']) * q['side']
        eq[j] = e

        if k + 1 < len(prep[coins[0]]['c']):
            for co in coins:
                if co in openp or co in pend:
                    continue
                s = prep[co]['sig'][k]
                if s != 0:
                    pend[co] = s

    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak
    R = np.array([t[1] for t in trades]) if trades else np.array([])
    w, l = (R[R > 0].sum(), -R[R < 0].sum()) if len(R) else (0.0, 0.0)
    return dict(
        tot=float(eq[-1] / capital - 1), mdd=float(dd.max()) if n else np.nan,
        n_tr=len(R), exp_r=float(R.mean()) if len(R) else np.nan,
        pf=float(w / l) if l > 0 else np.nan,
        wr=float((R > 0).mean()) if len(R) else np.nan,
        exposure=float(used.mean()), eq=eq, dd=dd, R=R, trades=trades)


# ─── границы окон по индексам баров ─────────────────────────────────

def _key(ts) -> int:
    """Ключ месяца: year*12 + month, month 1..12 (как в key() ниже)."""
    return ts.year * 12 + ts.month


def _unkey(k: int) -> tuple[int, int]:
    """Обратное преобразование ключа в (год, месяц 1..12)."""
    return k // 12, k % 12


def month_span(idx, start_key: int, n_months: int) -> tuple[int, int]:
    """Индексы баров месяцев [start_key, start_key+n_months)."""
    s, e = start_key, start_key + n_months
    lo = next((i for i, t in enumerate(idx) if _key(t) >= s), len(idx))
    hi = next((i for i, t in enumerate(idx) if _key(t) >= e), len(idx))
    return lo, hi


def main():
    H24.EXCLUDE_14 = {'XMR', 'LTC', 'BTC', 'SOL', 'TON', 'APT', 'ETH'}
    data, idx = H24.load()
    coins = sorted(data)

    P('=' * 104)
    P('H28. WALK-FORWARD + MONTE CARLO — Donchian pool10')
    P('=' * 104)
    P('')
    P(f'данные {idx[0].date()} .. {idx[-1].date()}  ({len(idx)} баров 4H, {len(coins)} монет)')
    P(f'сетка: lb={LBS} trail={TRAILS} hold={HOLDS} → {len(GRID)} комбинаций')
    P(f'train {TRAIN_MONTHS} мес. → test {TEST_MONTHS} мес. × {N_WINDOWS} окон, риск {RISK*100:.2f}%')
    P('')

    # ─── 1. верификация симулятора ──────────────────────────────────
    P('=' * 104)
    P('1. ВЕРИФИКАЦИЯ СИМУЛЯТОРА (обязана совпасть с H26)')
    P('=' * 104)
    prep = {k: prep_one(v, BASE['lb'], BASE['atrn']) for k, v in data.items()}
    full = simulate(prep, idx, BASE['lb'], BASE['trail'], BASE['hold'])
    exp = dict(n_tr=1827, exp_r=0.1690, pf=1.488, tot=1.585, mdd=0.1304)
    P(f'{"метрика":<14}{"H28":>12}{"H26":>12}{"совпало":>10}')
    P('-' * 104)
    ok_all = True
    for k, e in exp.items():
        got = full[k]
        good = abs(got - e) < (0.5 if k == 'n_tr' else 0.0015)
        ok_all &= good
        P(f'{k:<14}{got:>12.4f}{e:>12.4f}{"да" if good else "НЕТ":>10}')
    P('-' * 104)
    P(f'вердикт: {"симулятор воспроизводит H26" if ok_all else "РАСХОЖДЕНИЕ — дальнейшие числа недействительны"}')
    P('')
    if not ok_all:
        OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
        return 1

    # ─── 2. окна ───────────────────────────────────────────────────
    # Раскладка: первые TRAIN_MONTHS месяцев — только train, дальше
    # N_WINDOWS окон по TEST_MONTHS месяцев, без дыр и перекрытий.
    # train расширяется: для окна k обучение = всё ДО начала окна.
    first_test_key = _key(idx[0]) + TRAIN_MONTHS
    windows = []
    for w in range(N_WINDOWS):
        sk = first_test_key + w * TEST_MONTHS
        lo, hi = month_span(idx, sk, TEST_MONTHS)
        if hi - lo < 50:
            break
        windows.append((w + 1, lo, lo, hi, sk))
    P(f'окна: {len(windows)} шт, train расширяется с '
      f'{TRAIN_MONTHS} до {TRAIN_MONTHS + (len(windows)-1)*TEST_MONTHS} мес.')
    P('')

    P('=' * 104)
    P('2. WALK-FORWARD: перебор на train, применение на test')
    P('=' * 104)
    P(f'{"окно":<5}{"train до":<12}{"test":<24}{"выбрано":<20}'
      f'{"сделок":>7}{"expR":>8}{"PF":>7}{"WR":>7}{"итог":>9}{"DD":>7}')
    P('-' * 104)

    rows = []
    for w, tr_end, lo, hi, sk in windows:
        best, best_score = None, -np.inf
        for prm in GRID:
            pp = {k: prep_one(v, prm['lb'], prm['atrn']) for k, v in data.items()}
            r = simulate(pp, idx[:tr_end], prm['lb'], prm['trail'], prm['hold'])
            if r['n_tr'] < MIN_TRADES_TRAIN:
                continue
            # при равенстве expectancy берём меньший lb — проще и устойчивее
            if r['exp_r'] > best_score + 1e-9 or (
                    abs(r['exp_r'] - best_score) <= 1e-9 and best is not None
                    and prm['lb'] < best['lb']):
                best, best_score = prm, r['exp_r']
        if best is None:
            P(f'окно {w}: ни одна комбинация не набрала {MIN_TRADES_TRAIN} сделок')
            continue

        pp = {k: prep_one(v, best['lb'], best['atrn']) for k, v in data.items()}
        oos = simulate(pp, idx[lo:hi], best['lb'], best['trail'], best['hold'], offset=lo)
        bl = simulate(prep, idx[lo:hi], BASE['lb'], BASE['trail'], BASE['hold'], offset=lo)
        dtr = f"{idx[tr_end-1].date()}"
        y, m = _unkey(sk)
        dto = f"{idx[lo].date()}..{idx[hi-1].date()}"
        sel = f"lb{best['lb']}/tr{best['trail']}/h{best['hold']}"
        P(f'{w:<5}{dtr:<12}{dto:<24}{sel:<20}'
          f'{oos["n_tr"]:>7}{oos["exp_r"]:>8.3f}{oos["pf"]:>7.3f}'
          f'{oos["wr"]*100:>6.1f}%{oos["tot"]*100:>8.1f}%{oos["mdd"]*100:>6.1f}%')
        rows.append(dict(w=w, sel=best, oos=oos, bl=bl, lo=lo, hi=hi))
    P('-' * 104)
    P('')

    # контроль: окна не должны пересекаться и должны идти подряд
    ov = [(rows[i]['w'], rows[i+1]['w'])
          for i in range(len(rows)-1) if rows[i]['hi'] > rows[i+1]['lo']]
    P(f'контроль окон: пересечений {len(ov)} {ov if ov else "(окна идут подряд)"}')
    P('')

    # ─── 3. вердикт ────────────────────────────────────────────────
    P('=' * 104)
    P('3. ПРИЁМКА (критерий: >= 4 из 6 окон с PF >= 1)')
    P('=' * 104)
    n_ok = sum(1 for r in rows if r['oos']['pf'] >= 1.0)
    n_tot = len(rows)
    P(f' adaptive (параметры переотбирались):  PF >= 1 в {n_ok} из {n_tot} окон')
    nb = sum(1 for r in rows if r['bl']['pf'] >= 1.0)
    P(f' baseline (фикс lb20/tr2.0/h30):       PF >= 1 в {nb} из {n_tot} окон')
    P('')
    P(f'{"окно":<6}{"adaptive PF":>13}{"expR":>8}{"сделок":>8}'
      f'{"baseline PF":>14}{"expR":>8}{"сделок":>8}{"adaptive-баз":>14}')
    P('-' * 104)
    for r in rows:
        o, b = r['oos'], r['bl']
        P(f'{r["w"]:<6}{o["pf"]:>13.3f}{o["exp_r"]:>8.3f}{o["n_tr"]:>8}'
          f'{b["pf"]:>14.3f}{b["exp_r"]:>8.3f}{b["n_tr"]:>8}'
          f'{o["exp_r"] - b["exp_r"]:>14.3f}')
    P('-' * 104)

    # ─── агрегаты: НЕ суммы, а честная склейка ─────────────────────
    # PF, winrate, expectancy и DD нельзя складывать по окнам (PF 9.06 и
    # WR 252% —absurd). Склеиваем equity-кривые окном к окну: каждое окно
    # стартует со свежего капитала, поэтому кривые нормируем на 1 и
    # домножаем. Тогда доходность compounds, а DD считается по общей кривой.
    def chain(rows, src):
        eq_all, R_all = [], []
        acc = 1.0
        for r in rows:
            e = r[src]['eq']
            if len(e) == 0:
                continue
            peak = np.maximum.accumulate(e)
            dd = (peak - e) / peak
            eq_all.append(acc * e / e[0])
            acc *= e[-1] / e[0]
            R_all.append(r[src]['R'])
        if not eq_all:
            return None
        E = np.concatenate(eq_all)
        R = np.concatenate(R_all) if R_all else np.array([])
        w, l = (R[R > 0].sum(), -R[R < 0].sum()) if len(R) else (0.0, 0.0)
        peak = np.maximum.accumulate(E)
        return dict(tot=acc - 1, mdd=float(((peak - E) / peak).max()),
                    n_tr=int(len(R)), exp_r=float(R.mean()) if len(R) else np.nan,
                    pf=float(w / l) if l > 0 else np.nan,
                    wr=float((R > 0).mean()) if len(R) else np.nan)

    ca, cb = chain(rows, 'oos'), chain(rows, 'bl')
    P('ВЫБОР ПАРАМЕТРОВ ПРОТИВ ФИКСА (все OOS-окна, склейка)')
    P('-' * 104)
    P(f'{"метрика":<20}{"adaptive":>14}{"baseline (live)":>18}')
    P('-' * 104)
    for k, lab, f in (('n_tr', 'сделок', '{:d}'), ('exp_r', 'expectancy, R', '{:.3f}'),
                      ('pf', 'PF', '{:.3f}'), ('wr', 'winrate', '{:.1%}'),
                      ('tot', 'доходность compound', '{:.1%}'),
                      ('mdd', 'макс. DD по общей кривой', '{:.1%}')):
        P(f'{lab:<20}' + f.format(ca[k]).rjust(14) + f.format(cb[k]).rjust(18))
    P('-' * 104)
    P('')

    same = sum(1 for r in rows
               if r['sel']['lb'] == BASE['lb'] and r['sel']['trail'] == BASE['trail']
               and r['sel']['hold'] == BASE['hold'])
    P(f'сетка выбрала базовые (lb20/tr2.0/h30) в {same} из {len(rows)} окон')
    P(f'выбранные lb по окнам: {[r["sel"]["lb"] for r in rows]}')
    P('')

    # ─── 4. Monte Carlo на OOS ─────────────────────────────────────
    P('=' * 104)
    P('4. MONTE CARLO — 3000 итераций, блочная перетасовка (block=50, seed=20260926)')
    P('=' * 104)

    def mc_report(label, R):
        tots, mdds = H24.mc(R, RISK)
        P(f'--- {label}  ({len(R)} сделок) ---')
        P(f'{"":<14}{"p5":>10}{"p25":>10}{"med":>10}{"p75":>10}{"p95":>10}')
        P(f'{"доходность":<14}' + ''.join(
            f'{np.percentile(tots, q)*100:>9.1f}%' for q in (5, 25, 50, 75, 95)))
        P(f'{"макс. DD":<14}' + ''.join(
            f'{np.percentile(mdds, q)*100:>9.1f}%' for q in (5, 25, 50, 75, 95)))
        P(f'  P(DD > 20%) = {(mdds > 0.20).mean()*100:.1f}%   '
          f'P(DD > 30%) = {(mdds > 0.30).mean()*100:.1f}%')
        p5 = np.percentile(mdds, 5)
        p95 = np.percentile(mdds, 95)
        P(f'  критерий заказчика: DD p5 = {p5*100:.1f}%  '
          f'{" > 25% → РИСК СНИЖАТЬ" if p5 > 0.25 else "≤ 25% → риск не снижать"}')
        P(f'  для справки DD p95 = {p95*100:.1f}%')
        P('')
        return p5

    R_ad = np.concatenate([r['oos']['R'] for r in rows]) if rows else np.array([])
    R_bl = np.concatenate([r['bl']['R'] for r in rows]) if rows else np.array([])
    p5_ad = mc_report('adaptive, все OOS-окна', R_ad)
    mc_report('baseline, все OOS-окна', R_bl)
    mc_report('полный период in-sample (для сверки с H26)', full['R'])

    P('=' * 104)
    P('ИТОГ')
    P('=' * 104)
    P(f'  WF-приёмка (>=4/6 окон PF>=1): {"ПРОЙДЕНА" if n_ok >= 4 else "НЕ ПРОЙДЕНА"}'
      f' — adaptive {n_ok}/{n_tot}, baseline {nb}/{n_tot}')
    P(f'  MC DD p5 = {p5_ad*100:.1f}%: {"снижать риск" if p5_ad > 0.25 else "риск оставляем 0.35%"}')
    P(f'  OOS (24 мес. вне обучения) compound: adaptive {ca["tot"]*100:+.1f}%, '
      f'baseline {cb["tot"]*100:+.1f}%, макс. DD {ca["mdd"]*100:.1f}% / {cb["mdd"]*100:.1f}%')
    P('')
    P('ОГРАНИЧЕНИЯ (почему эти цифры нельзя читать как прогноз дохода):')
    P('  1. Пул отобран в H25 на полном окне in-sample. Переотбор пула внутри')
    P('     каждого train-окна здесь НЕ делался — это главный невычищенный источник')
    P('     переобучения, и он может быть заметнее, чем перебор lb/trail/hold.')
    P('  2. Первое окно обучается всего на 6 месяцах (~250 сделок на весь пул),')
    P('     поэтому его выбор параметров шумный.')
    P('  3. Блок перетасовки длиной 50 сделок сохраняет локальную корреляцию,')
    P('     но не моделирует смену режима рынка.')
    P('  4. Funding, адверс-селекшн и стресс-периоды не моделируются.')

    OUT.write_text('\n'.join(_l) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT.resolve()}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
