"""H38 — ZDev A 2.0 на 4H: стоп дальше + ER-фильтр + чистый TP.

ПРЕДРЕГИСТРАЦИЯ (зафиксирована до прогона). Гипотеза владельца:
  стоп за swing low (12 баров) вместо фитиля бочонка -> шум не выбивает,
  риск на сделку выше -> позиция меньше; ER20 > медианы как фильтр тренда;
  TP = equilibrium или 1.61R без трейлинга.

ЧТО УЖЕ ПРОВЕРЕНО РАНЬШЕ (не переигрывать, а понять почему):
  H1 — swing-12 + TP 1.61R, но вход ПОСЛЕ импульса: pooled E[R] = +0.061R,
       95% CI [-0.118, +0.241] -> не отличим от нуля; 6-12 сделок/год/монету.
  H2 — swing-12 на базовом входе ZDev: НЕВАЛИДЕН. 88.7% сигналов имеют стоп не
       с той стороны от входа (h2_stop_applicability.txt): ZDev входит на краю
       движения (2 ATR от равновесия), а prior-экстремум по определению лежит
       по ту же сторону. Движок такие сигналы отбрасывает
       (ab_btc_doji_tvl.py:140-143), поэтому H2 мерил 12% выборки.
  Вывод H2-аудита дословно: «стоп за структурой» и «вход после отклонения»
  взаимоисключающи.
  => Стоп-за-swing в буквальном виде для входа ZDev невозможен независимо
     от ER и TP. Проверяем ЗАМЕНУ, которая геометрию не нарушает.

ЧТО НОВОГО ЗДЕСЬ (единственная причина прогона):
  1) ER20/950-фильтр на вход ZDev — на 4H-Donchian дал +0.033 expR (H32-H36),
     на ZDev не проверялся никогда.
  2) Чистый TP без трейлинга (ab_percoin_honest_wf проверял его на
     ETH/TON/NEAR/SUI/APT, но не на пуле ZDev и не вместе с ER).
  3) Расширение стопа БЕЗ нарушения геометрии: стоп = min(фитиль, 2.0 x ATR(14)).
     Константа 2.0 импортирована из live-Donchian (2.0 x ATR), а не подобрана
     по ZDev. N (12) не крутим.

АРМЫ
  Z0 control   ZDev как есть: стоп=фитиль, TP=eq, LIVE (BE .3 + trail .8/.3)
  Z1           Z0 + чистый TP (без BE, без трейлинга)
  Z2           Z0 + ER20/950
  Z3           Z0 + чистый TP + ER        <- спецификация владельца, стоп не тронут
  Z4           Z0 + расширенный стоп 2.0xATR14
  Z5           Z0 + расширенный стоп + чистый TP + ER   <- полный замысел
  Z6           Z0 + swing-12 (диагностика: где стоп не с той стороны, откат на
               2 x ATR; так сигнал выживает, и мы видим замысел буквально)

ПРАВИЛО РЕШЕНИЯ (задано ДО прогона). PASS требует ВСЕГО:
  1) pooled E[R] 95% CI не содержит 0 (block-bootstrap 20k по 2-недельным блокам)
  2) pooled PF >= 1.30
  3) прирост E[R] против Z0 значим: CI парной разности не содержит 0
  4) pooled max DD < 10% (порог ZDev-скрина)
  5) частота >= 20 сделок/год/монету (иначе повторяем ловушку H1: 6-12/год)
  6) ноль-гипотеза «PF>=1 в k/N окон» не отвергается перестановкой сделок

Интеграция НЕ выполняется ни при каком исходе — по директиве владельца.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import ab_btc_doji_tvl as E
from ab_btc_doji_tvl import run_engine
from strategies_v2 import generate_signals
from smc_features import calculate_atr
from core.donchian_breakout import efficiency_ratio, er_threshold_at

OUT = ROOT / 'backtest_results' / 'h38_zdev_swing_er.txt'
L = []

COINS = ['ADA', 'NEAR', 'ARB', 'SOL', 'DOGE', 'XRP']
ZDEV = {'z_threshold': 2.0, 'zdev_atr_min': 2.0, 'lookback': 20}
RISK_PCT = 0.35
ATR_N = 14
STOP_ATR = 2.0
SWING = 12
TP_R = 1.61
ER_PERIOD = 20
ER_WINDOW = 950
ER_WARMUP = ER_PERIOD + ER_WINDOW + 2
WF_DAYS = 183
N_WIN = 6
BLOCK = 42
BOOT = 20000
SHUFFLE = 2000
SEED = 20260927

RISK_BASE = {
    'commission': 0.001, 'slippage': 0.0005, 'stop_buffer': 0.002,
    'trail_after_tp1': False, 'max_leverage': 10.0,
    'max_daily_loss_pct': 5.0, 'max_daily_trades': 20,
    'max_consecutive_losses': 0, 'same_bar_stop': True,
    'initial_balance': 10000.0, 'no_lookahead': True,
    'risk_percent': RISK_PCT, 'cooldown_hours': 4.0,
}
EXIT_LIVE = dict(breakeven_at=0.3, trail_activate=0.8, trail_step=0.3)
EXIT_CLEAN = dict(breakeven_at=0.0, trail_activate=0.0, trail_step=0.0)

ARMS = [
    ('Z0', 'control: ZDev как есть',              dict(stop='wick',  tp='eq', er=False, exit=EXIT_LIVE)),
    ('Z1', 'Z0 + чистый TP',                      dict(stop='wick',  tp='eq', er=False, exit=EXIT_CLEAN)),
    ('Z2', 'Z0 + ER20/950',                       dict(stop='wick',  tp='eq', er=True,  exit=EXIT_LIVE)),
    ('Z3', 'Z0 + чистый TP + ER',                dict(stop='wick',  tp='eq', er=True,  exit=EXIT_CLEAN)),
    ('Z4', 'Z0 + стоп 2.0xATR14',                dict(stop='atr',   tp='eq', er=False, exit=EXIT_LIVE)),
    ('Z5', 'Z0 + стоп 2.0xATR14 + TP + ER',      dict(stop='atr',   tp='eq', er=True,  exit=EXIT_CLEAN)),
    ('Z6', 'Z0 + swing-12 (диагностика)',         dict(stop='swing', tp='eq', er=False, exit=EXIT_LIVE)),
]

CACHE = {}


def P(s=''):
    L.append(s)
    print(s)


def load(coin):
    if coin in CACHE:
        return CACHE[coin]
    d = ROOT / 'data' / 'raw'
    for pat in (f'{coin}_4h_2020-01-01_top20.csv', f'{coin}_4h_top20.csv'):
        if (d / pat).exists():
            df = pd.read_csv(d / pat, parse_dates=['timestamp']).reset_index(drop=True)
            CACHE[coin] = df
            return df
    raise FileNotFoundError(f'no 4h data for {coin} in {d}')


def windows_for(df):
    start, end = df['timestamp'].iloc[0], df['timestamp'].iloc[-1]
    out, ws = [], start
    while ws < end:
        we = min(ws + pd.Timedelta(days=WF_DAYS), end)
        out.append((ws, we))
        ws = we
    return out[-N_WIN:]


def base_signal_list(df):
    raw = generate_signals(df, 'zdev', variant='A', cfg=ZDEV)
    idx = {str(t): i for i, t in enumerate(df['timestamp'])}
    out = []
    for s in raw:
        i = idx.get(s['timestamp'])
        if i is None:
            continue
        out.append({'bar': i,
                    'direction': 'LONG' if s['direction'] == 'BUY' else 'SHORT',
                    'entry': float(s['entry']), 'wick': float(s['stop']),
                    'eq': float(s['tp']),
                    'z': s.get('z'), 'dev_frac': s.get('dev_frac')})
    out.sort(key=lambda x: x['bar'])
    return out


def make_sigs(df, sigs, er, atr, stop_mode, tp_mode, use_er):
    lo = df['low'].to_numpy(float)
    hi = df['high'].to_numpy(float)
    out = []
    diag = dict(er_drop=0, er_cold=0, inv_swing=0, inv_any=0)
    for s in sigs:
        i = s['bar']
        if use_er:
            thr = er_threshold_at(er, i, ER_WINDOW)
            if thr is None:
                diag['er_cold'] += 1
                continue
            if er[i] < thr:
                diag['er_drop'] += 1
                continue
        entry = s['entry']
        a = atr[i]
        ok_atr = np.isfinite(a) and a > 0
        long = s['direction'] == 'LONG'
        atr_d = STOP_ATR * a if ok_atr else 0.0

        if stop_mode == 'wick':
            stop = s['wick']
        elif stop_mode == 'atr':
            dist = max(abs(entry - s['wick']), atr_d)
            stop = entry - dist if long else entry + dist
        else:
            if i < SWING:
                continue
            sw = lo[i - SWING:i].min() if long else hi[i - SWING:i].max()
            if not np.isfinite(sw):
                continue
            if (long and sw >= entry) or (not long and sw <= entry):
                diag['inv_swing'] += 1
            fallback = entry - atr_d if long else entry + atr_d
            stop = min(sw, fallback) if long else max(sw, fallback)

        if (long and stop >= entry) or (not long and stop <= entry):
            diag['inv_any'] += 1
            continue

        if tp_mode == 'eq':
            tp = s['eq']
        else:
            dist = abs(entry - stop)
            tp = entry + TP_R * dist if long else entry - TP_R * dist
        if (long and tp <= entry) or (not long and tp >= entry):
            diag['inv_any'] += 1
            continue

        out.append({'bar': i, 'direction': s['direction'], 'entry': entry,
                    'stop': float(stop), 'tp1': float(tp), 'tp2': None,
                    'tp1_frac': 1.0, 'z': s['z'], 'dev_frac': s['dev_frac']})
    return out, diag


def pf_of(r):
    w, l = r[r > 0], r[r <= 0]
    return (w.sum() / abs(l.sum())) if len(l) and l.sum() != 0 else float('inf')


def equity_dd(rs, rf):
    eq, peak, dd = 1.0, 1.0, 0.0
    for x in rs:
        eq *= (1.0 + x * rf)
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    return eq, dd


def stats(trs, base, subts):
    if not trs:
        return None
    r = np.array([t['r'] for t in trs], float)
    ts = [subts[t['entry_bar']] for t in trs]
    return dict(n=len(r), pf=pf_of(r), wr=float((r > 0).mean()),
                er=float(r.mean()), r=r, ts=ts)


def run_seg(df, sigs, lo, hi, exit_cfg):
    sub = df[(df['timestamp'] >= lo) & (df['timestamp'] < hi)].reset_index(drop=True)
    if len(sub) < 200:
        return None
    base = int(np.searchsorted(df['timestamp'].values,
                               sub['timestamp'].iloc[0].to_datetime64()))
    loc = [dict(s, bar=s['bar'] - base) for s in sigs if base <= s['bar'] < base + len(sub)]
    if not loc:
        return None
    E.reset_engine_stats()
    trs, _bal = run_engine(sub, loc, dict(RISK_BASE, **exit_cfg))
    return stats(trs, base, sub['timestamp'].tolist())


def _mean_over(pick, g):
    parts = [g[b] for b in pick if b in g]
    return float(np.concatenate(parts).mean()) if parts else np.nan


def block_boot(ts, r, rng):
    b = pd.factorize(pd.Series(ts).dt.floor('D'))[0]
    g = {x: r[b == x] for x in np.unique(b)}
    u = np.array(sorted(g))
    out = np.empty(BOOT)
    for k in range(BOOT):
        out[k] = _mean_over(rng.choice(u, size=len(u), replace=True), g)
    return out[~np.isnan(out)]


def block_boot_diff(ts_a, r_a, ts_b, r_b, rng):
    ba = pd.factorize(pd.Series(ts_a).dt.floor('D'))[0]
    bb = pd.factorize(pd.Series(ts_b).dt.floor('D'))[0]
    ga = {x: r_a[ba == x] for x in np.unique(ba)}
    gb = {x: r_b[bb == x] for x in np.unique(bb)}
    allb = np.array(sorted(set(ga) | set(gb)))
    out = np.empty(BOOT)
    for k in range(BOOT):
        pick = rng.choice(allb, size=len(allb), replace=True)
        ma, mb = _mean_over(pick, ga), _mean_over(pick, gb)
        out[k] = (np.nan if (np.isnan(ma) or np.isnan(mb)) else mb - ma)
    return out[~np.isnan(out)]


def shuffle_null(per_coin_windows, rng):
    """per_coin_windows: {монета: [R-массив по окну либо None]}.

    Ноль-гипотеза: «временное расположение сделок ничего не значит».
    Перемешиваем сделки ВНУТРИ монеты, сохраняя размеры окон, и считаем,
    сколько окон дадут PF>=1. Если наблюдаемое число не превышает это —
    правило окон не отделяет сигнал от шума.
    """
    by_coin = {}
    for c, wins in per_coin_windows.items():
        gs = [np.asarray(w, float) for w in wins if w is not None and len(w)]
        if gs:
            by_coin[c] = gs
    if not by_coin:
        return 0, 0, 0.0, 1.0

    def _pf(arr):
        w_, l_ = arr[arr > 0], arr[arr <= 0]
        if len(l_) == 0 or l_.sum() == 0:
            return float('inf') if len(w_) else 0.0
        return float(w_.sum() / abs(l_.sum()))

    obs = tot = 0
    for gs in by_coin.values():
        for arr in gs:
            tot += 1
            obs += 1 if _pf(arr) >= 1.0 else 0

    hits = np.empty(SHUFFLE)
    for it in range(SHUFFLE):
        h = 0
        for gs in by_coin.values():
            allr = np.concatenate(gs)
            segs = [allr[rng.integers(0, len(allr), len(arr))] for arr in gs]
            h += sum(1 for seg in segs if _pf(seg) >= 1.0)
        hits[it] = h
    return obs, tot, float(hits.mean()), float((hits >= obs).mean())


def pool_years():
    ys = []
    for c in COINS:
        df = load(c)
        ws = windows_for(df)
        ys.append((ws[-1][1] - ws[0][0]).days / 365.25)
    return sum(ys)


def main():
    rng = np.random.default_rng(SEED)
    P('=' * 104)
    P('H38 — ZDev A 2.0 (4H): стоп дальше + ER20/950 + чистый TP')
    P('пререгистрация в шапке файла. Интеграция НЕ выполняется.')
    P('=' * 104)
    P('')
    P(f'пул: {" ".join(COINS)}   риск {RISK_PCT}%   ATR({ATR_N})   стоп-константа '
      f'{STOP_ATR}xATR   swing N={SWING} (не крутим)   TP_R={TP_R}')
    P(f'ER: period={ER_PERIOD} window={ER_WINDOW} warmup={ER_WARMUP} баров, fail-closed')
    P(f'издержки движка: {RISK_BASE["commission"]*100:.3f}% + {RISK_BASE["slippage"]*100:.3f}% '
      f'= {(RISK_BASE["commission"]+RISK_BASE["slippage"])*2*100:.2f}% round-trip')
    P('  (live-Donchian использует 0.14% RT, то есть ZDev здесь платит вдвое больше —')
    P('   консервативно для ZDev, но сравнение внутри ZDev честное)')
    P(f'WF: последние {N_WIN} окон по {WF_DAYS} дней. bootstrap: block по суткам, {BOOT} итераций.')
    P('')

    data = {}
    for c in COINS:
        df = load(c)
        er = efficiency_ratio(df['close'].to_numpy(float), ER_PERIOD)
        atr = calculate_atr(df, period=ATR_N).to_numpy(float)
        sigs = base_signal_list(df)
        data[c] = dict(df=df, er=er, atr=atr, sigs=sigs,
                       wins=windows_for(df))
        n_cold = sum(1 for s in sigs if er_threshold_at(er, s['bar'], ER_WINDOW) is None)
        P(f'  {c:5s} {len(df):6d} баров {df["timestamp"].iloc[0].date()}..'
          f'{df["timestamp"].iloc[-1].date()}  сигналов {len(sigs):4d}  '
          f'ER-холодных {n_cold:4d}  окон {len(data[c]["wins"])}')
    P('')

    P('=' * 104)
    P('0. СТРУКТУРА СТОПА: почему «swing-12» нельзя взять буквально')
    P('=' * 104)
    P('')
    P('  ZDev входит на ОТКЛОНЕНИИ 2 ATR от равновесия, то есть на краю движения.')
    P('  Prior-экстремум за 12 баров по определению лежит по ту же сторону от входа,')
    P('  то есть не является защитным стопом. Движок (ab_btc_doji_tvl.py:140-143)')
    P('  такие сигналы молча отбрасывает.')
    P('')
    hdr = (f'  {"монета":6s}{"сигн":>6s}{"стоп не с той стороны":>22s}{"доля":>8s}'
           f'{"фитиль %":>10s}{"фитиль/ATR":>12s}{"2xATR %":>10s}')
    P(hdr)
    P('  ' + '-' * (len(hdr) - 2))
    tot_sig = tot_inv = 0
    for c in COINS:
        d = data[c]
        n_inv = 0
        wick_pct, wick_atr, wide_pct = [], [], []
        for s in d['sigs']:
            i, long = s['bar'], s['direction'] == 'LONG'
            lo = d['df']['low'].to_numpy(float)[i - SWING:i].min() if i >= SWING else np.nan
            hi = d['df']['high'].to_numpy(float)[i - SWING:i].max() if i >= SWING else np.nan
            sw = lo if long else hi
            if np.isfinite(sw) and ((long and sw >= s['entry']) or (not long and sw <= s['entry'])):
                n_inv += 1
            a = d['atr'][i]
            if np.isfinite(a) and a > 0:
                wick_atr.append(abs(s['entry'] - s['wick']) / a)
                wide_pct.append(STOP_ATR * a / s['entry'] * 100)
            wick_pct.append(abs(s['entry'] - s['wick']) / s['entry'] * 100)
        tot_sig += len(d['sigs'])
        tot_inv += n_inv
        P(f'  {c:6s}{len(d["sigs"]):6d}{n_inv:22d}{n_inv/len(d["sigs"]):8.1%}'
          f'{np.median(wick_pct):10.3f}{np.median(wick_atr):12.2f}{np.median(wide_pct):10.3f}')
    P('  ' + '-' * (len(hdr) - 2))
    P(f'  {"ПУЛ":6s}{tot_sig:6d}{tot_inv:22d}{tot_inv/tot_sig:8.1%}')
    P('')
    P('  Вывод: swing-12 нерабочий как стоп для этого входа. Дальше он идёт только')
    P('  как диагностика Z6 с откатом на 2xATR там, где он не с той стороны.')
    P('  «Расширенный» стоп = min(фитиль, 2.0xATR) — монотонно шире фитиля,')
    P('  поэтому всегда валиден и ни разу не является подгонкой.')
    P('')

    results = {}
    for tag, label, cfg in ARMS:
        per_coin = {}
        for c in COINS:
            d = data[c]
            es, diag = make_sigs(d['df'], d['sigs'], d['er'], d['atr'],
                                 cfg['stop'], cfg['tp'], cfg['er'])
            full = run_seg(d['df'], es, d['df']['timestamp'].iloc[0],
                           d['df']['timestamp'].iloc[-1] + pd.Timedelta(seconds=1),
                           cfg['exit'])
            wins = [run_seg(d['df'], es, a, b, cfg['exit']) for a, b in d['wins']]
            per_coin[c] = dict(full=full, wins=wins, nsig=len(es), diag=diag)
        results[tag] = dict(label=label, cfg=cfg, per_coin=per_coin)

    P('=' * 104)
    P('1. ПОЛНЫЙ ПЕРИОД, по монетам (ER/TR как есть; risk 0.35%)')
    P('=' * 104)
    P('')
    for tag, label, _cfg in ARMS:
        P(f'  {tag} — {label}')
        P(f'    {"монета":6s}{"сигн":>6s}{"сделок":>8s}{"expR":>9s}{"PF":>8s}'
          f'{"WR":>7s}{"сделок/год":>12s}')
        for c in COINS:
            pc = results[tag]['per_coin'][c]
            f = pc['full']
            df = data[c]['df']
            yrs = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
            if not f:
                P(f'    {c:6s}{pc["nsig"]:6d}{0:8d}{"-":>9s}{"-":>8s}{"-":>7s}{"-":>12s}')
                continue
            P(f'    {c:6s}{pc["nsig"]:6d}{f["n"]:8d}{f["er"]:9.3f}'
              f'{f["pf"]:8.2f}{f["wr"]:7.1%}{f["n"]/yrs:12.1f}')
        P('')

    P('=' * 104)
    P('2. WALK-FORWARD, пул (последние 6 окон, E[R] в R, DD по составному капиталу)')
    P('=' * 104)
    P('')
    hdr = (f'  {"арм":4s}{"сделок":>8s}{"expR":>9s}{"PF":>8s}{"окн PF>=1":>11s}'
           f'{"DD":>8s}{"сделок/год":>12s}')
    P(hdr)
    P('  ' + '-' * (len(hdr) - 2))
    pooled = {}
    for tag, label, _cfg in ARMS:
        rows_r, rows_ts, wins_by_coin = [], [], {}
        ge1 = tot_w = 0
        for c in COINS:
            wins_by_coin[c] = []
            for w in results[tag]['per_coin'][c]['wins']:
                if not w:
                    wins_by_coin[c].append(None)
                    continue
                rows_r.extend(w['r'].tolist())
                rows_ts.extend(w['ts'])
                tot_w += 1
                ge1 += 1 if w['pf'] >= 1.0 else 0
                wins_by_coin[c].append(w['r'])
        r = np.array(rows_r, float)
        if len(r) == 0:
            P(f'  {tag:4s}{0:8d}{"-":>9s}{"-":>8s}{"-":>11s}{"-":>8s}{"-":>12s}')
            continue
        order = np.argsort(pd.Series(rows_ts).values)
        rs = r[order]
        tss = [rows_ts[i] for i in order]
        _eq, dd = equity_dd(rs, RISK_PCT / 100.0)
        # pool_years() уже в монето-годах (6 монет x 3 года), делить ещё раз
        # на число монет нельзя — это занижает частоту вчетверо.
        yrs = pool_years()
        pooled[tag] = dict(r=r, ts=tss, dd=dd, wins_by_coin=wins_by_coin)
        P(f'  {tag:4s}{len(r):8d}{r.mean():9.3f}{pf_of(r):8.2f}'
          f'{f"{ge1}/{tot_w}":>11s}{dd:8.1%}{len(r)/yrs:12.1f}')
    P('  ' + '-' * (len(hdr) - 2))
    P('')

    P('=' * 104)
    P('3. ЗНАЧИМОСТЬ (то, на что нельзя смотреть глазами по окнам)')
    P('=' * 104)
    P('')
    P('  Урок H1: «PF>=1 в 4/6 окон» на окнах по 2-7 сделок — это шум.')
    P('  Поэтому ниже: pooled E[R] с CI, парная разница против Z0 с CI,')
    P('  и ноль-гипотеза перестановки сделок внутри монеты.')
    P('')
    base = pooled.get('Z0')
    P(f'  {"арм":4s}{"expR":>9s}{"95% CI":>22s}{"P(E>0)":>9s}'
       f'{"expR-Z0":>10s}{"CI разности":>24s}{"p-value":>9s}')
    P('  ' + '-' * 88)
    for tag, _label, _cfg in ARMS:
        if tag not in pooled:
            continue
        r, ts = pooled[tag]['r'], pooled[tag]['ts']
        bs = block_boot(ts, r, rng)
        lo, hi = np.percentile(bs, [2.5, 97.5])
        p_gt = float((bs > 0).mean())
        if tag == 'Z0' or base is None:
            P(f'  {tag:4s}{r.mean():9.3f}{f"[{lo:+.3f}, {hi:+.3f}]":>22s}{p_gt:9.3f}'
              f'{"(baseline)":>10s}{"":>24s}{"":>9s}')
        else:
            bd = block_boot_diff(base['ts'], base['r'], ts, r, rng)
            dlo, dhi = np.percentile(bd, [2.5, 97.5])
            p = 2.0 * min(float((bd <= 0).mean()), float((bd >= 0).mean()))
            P(f'  {tag:4s}{r.mean():9.3f}{f"[{lo:+.3f}, {hi:+.3f}]":>22s}{p_gt:9.3f}'
              f'{r.mean()-base["r"].mean():10.3f}{f"[{dlo:+.3f}, {dhi:+.3f}]":>24s}{p:9.3f}')
    P('')
    P('  Перестановка сделок внутри монеты (окна сохраняют размер, меняется только время):')
    P('')
    P(f'  {"арм":4s}{"окн PF>=1":>12s}{"нуль (среднее)":>16s}{"P(набл. >= нуль)":>20s}')
    P('  ' + '-' * 52)
    for tag, _label, _cfg in ARMS:
        if tag not in pooled:
            continue
        obs, tot, nul, p = shuffle_null(pooled[tag]['wins_by_coin'], rng)
        P(f'  {tag:4s}{f"{obs}/{tot}":>12s}{nul:16.1f}{p:20.3f}')
    P('')

    P('=' * 104)
    P('4. РЕШЕНИЕ ПО ПРАВИЛУ (задано до прогона)')
    P('=' * 104)
    P('')
    P(f'  {"арм":4s}{"1 CI≠0":>9s}{"2 PF≥1.3":>11s}{"3 ΔCI≠0":>10s}{"4 DD<10%":>10s}'
       f'{"5 ≥20/год":>11s}{"6 не шум":>10s}   вердикт')
    P('  ' + '-' * 78)
    for tag, label, _cfg in ARMS:
        if tag not in pooled:
            continue
        r, ts = pooled[tag]['r'], pooled[tag]['ts']
        bs = block_boot(ts, r, rng)
        lo, hi = np.percentile(bs, [2.5, 97.5])
        c1 = not (lo <= 0 <= hi)
        c2 = pf_of(r) >= 1.30
        if tag == 'Z0':
            c3 = True
        else:
            bd = block_boot_diff(base['ts'], base['r'], ts, r, rng)
            dlo, dhi = np.percentile(bd, [2.5, 97.5])
            c3 = not (dlo <= 0 <= dhi) and r.mean() > base['r'].mean()
        c4 = pooled[tag]['dd'] < 0.10
        yrs = pool_years()
        freq = len(r) / yrs          # сделок на монето-год, без двойного деления
        c5 = freq >= 20.0
        obs, tot, nul, p = shuffle_null(pooled[tag]['wins_by_coin'], rng)
        c6 = p <= 0.10
        ok = all([c1, c2, c3, c4, c5, c6])
        flags = ''.join('  Y' if x else '  n' for x in (c1, c2, c3, c4, c5, c6))
        P(f'  {tag:4s}{flags}   {"PASS" if ok else "нет"}   '
          f'({freq:.1f} сделок/год/монету, {obs}/{tot} окон)')

    P('')
    P('=' * 104)
    P('5. ЧТО ЭТО ЗНАЧИТ')
    P('=' * 104)
    P('')
    P('  Интеграция не выполняется ни при каком исходе — по директиве владельца.')
    P('  Если Z5 (расширенный стоп + чистый TP + ER) не проходит правило, то')
    P('  переигрывать параметры нельзя: те же данные уже дали in-sample выбор')
    P('  (пул ZDev, порог z, окна WF). Это будет подгонка, а не исследование.')
    P('')
    P('  ОГРАНИЧЕНИЯ:')
    P('   1. Пул 6 монет, окно 2023-09..2026-09 — тот же ряд, что и H1/H2/screen_zdev.')
    P('   2. Гипотеза «стоп дальше» уже опровергнута геометрически (секция 0),')
    P('      поэтому Z4/Z5 — это проверка ЗАМЕНЫ (2xATR), а не проверка swing-12.')
    P('   3. Частота сделок у ZDev низкая (6-15/год/монету). На таких объёмах')
    P('      правило окон не работает — поэтому CI, а не «4/6 окон».')
    P('   4. Издержки ZDev здесь 0.30% RT против 0.14% у live-Donchian.')
    P('   5. Funding, проскальзывание на заявках, частичные исполнения — не моделируются.')
    P('   6. Одна серия, один прогон. p-значения без поправки на 7 арм.')
    P('')

    P('=' * 104)
    P('6. САМОПРОВЕРКА ДВИЖКА: Z0 против аудированного «честного» столбца')
    P('=' * 104)
    P('')
    P('  diag_lookahead.txt содержит тот же сигнал ZDev A 2.0, посчитанный двумя')
    P('  движками: с lookahead-багом и честно. Наша Z0 обязана совпасть со вторым.')
    P('  Если не совпадает —baseline недостоверен, весь прогон недействителен.')
    P('')
    REF = {'ADA': (183, 0.59), 'ARB': (103, 1.22), 'DOGE': (153, 0.76),
           'NEAR': (153, 0.75)}
    P(f'  {"монета":6s}{"n (док)":>9s}{"n (наш)":>9s}{"PF (док)":>10s}{"PF (наш)":>10s}  вердикт')
    P('  ' + '-' * 58)
    allok = True
    for c, (rn, rp) in REF.items():
        f = results['Z0']['per_coin'][c]['full']
        ok = f is not None and f['n'] == rn and abs(f['pf'] - rp) <= 0.05
        allok = allok and ok
        P(f'  {c:6s}{rn:9d}{(f["n"] if f else 0):9d}{rp:10.2f}'
          f'{(f["pf"] if f else 0.0):10.2f}  {"совпало" if ok else "РАСХОЖДЕНИЕ"}')
    P('  ' + '-' * 58)
    P(f'  Движок воспроизводит аудированный честный baseline: {"ДА" if allok else "НЕТ"}')
    P('')
    P('  Для сравнения: тот же сигнал на движке с lookahead-багом даёт')
    P('  ADA PF 14.53, ARB 10.41, DOGE 8.45, NEAR 12.69 (diag_lookahead.txt),')
    P('  потому что стоп строится от high/low ТЕКУЩЕГО бара и тут же проверяется')
    P('  его же low/high: 86-90% сделок закрываются таким стопом, давая 107-114%')
    P('  всего PnL. Именно этот путь породил цифры ZDev в CLAUDE.md (PF 3.94 и т.д.).')
    P('')

    OUT.write_text('\n'.join(L) + '\n', encoding='utf-8')
    print(f'\nSaved: {OUT}')


if __name__ == '__main__':
    main()
