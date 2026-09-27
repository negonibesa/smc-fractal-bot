# SMC Fractal Bot — Project Context

## What
Trading bot for Bybit Demo Trading, 4H candles. Production strategy is **Donchian 20** (20-bar channel breakout + ATR trailing). 10 coins live. `smc` and `zdev` paths remain in the code but are not live.

## Live Strategy → Donchian 20 + ER-вход (validated H24–H26, filter H32–H36)
```
entry:     close beyond 20-bar Donchian channel, fill at next bar open,
           ONLY IF Kaufman ER(20) >= median(ER(20) over previous 950 bars)
stop:      2.0 x ATR(20) at entry, ATR frozen at entry (no recalc)
trailing:  stop follows the extreme by 2 x ATR_entry
time-stop: 30 bars, exit at close
TP:        none (exit is stop or time-stop only)
risk:      0.35% of full equity per trade, sized = equity*risk/(2*ATR)
cap:       max 6 concurrent positions portfolio-wide
halt:      20% drawdown (the only circuit breaker)
costs:     taker 0.055% + slippage 0.015%/side = 0.14% round-trip
```
Pool 10: `ADA ARB DOGE ENA HBAR LINK NEAR SUI XLM XRP`

### ER-фильтр входа (H32/H33/H36, LIVE с 2026-09-27)
Пробои 20-канала зарабатывают только в трендовом рынке. Kaufman ER(20) против
медианы: тренд expR +0.319 (PF 2.12) vs хаос -0.097 (PF 0.79), p<0.0001, эффект
одинаков в обеих половинах по волатильности (H32).

Walk-forward H33 (порог на train → test, 6 окон OOS): baseline 1488 expR 0.161
PF 1.459 +107.2% DD 13.0% PF≥1 5/6 → вариант A 1244 expR 0.202 PF 1.604
+118.9% DD 10.8% PF≥1 6/6. MC: p5 52.4% vs 45.0%, DD p95 14.3% vs 16.6%,
P(DD>20%) 0.1% vs 1.1%.

В коде порог **скользящий** (медиана за 950 баров), не зашитая константа: H36
показал, что window=950 воспроизводит замороженный train-порог (expR 0.202 /
PF 1.609 / +118.1%). Бот тянет **1000 баров 4H** — лимит Bybit на один запрос
`/v5/market/kline`. Прогрев `warmup_bars = 20 + 950 + 2 = 972`.

- Фильтр режет **только вход**. Stop, trailing, time-stop, risk, cap не тронуты.
- Fail-closed: баров < прогрева → сигнала нет (лог уходит в debug, не в warning).
- `er_filter` по умолчанию `False` в датаклассе; включается глобальной секцией
  `donchian:` в `config/settings.yaml` — так фильтр applies на весь пул сразу.
- Пароль: `commission_haircut: 0.0` обязан лежать в секции `risk:`. Проверяется
  тестом — при вставке секции `donchian:` внутрь `risk:` он «съедает» ключи.

⚠️ ER-гипотеза выбрана на полной выборке 2024-04..2026-09. Walk-forward честен
по порогу, но сам вопрос «а не попробовать ли ER?» задан на всей выборке. На
Demo риск ограничен, на боевом пересмотреть через 3-6 месяцев forward-данных.

H26 baseline (до фильтра, risk 0.35%, cap 6): 1827 trades, expectancy +0.1690R,
WR 42.3%, PF 1.488, exposure 19.8%, max DD 13.0%. MC (3000 iters): return p5 74.6%
/ med 179.9% / p95 370.8%, DD p95 15.5%, P(DD>30%)=0.0%. vs pool14: expR
0.121→0.169, PF 1.34→1.49, DD p95 20.7%→15.5%.

Removed in H25 as unprofitable after costs (netR): SOL -0.023, TON -0.003, APT 0.001, ETH 0.047.
XMR/LTC/BTC not reinstated (netR -0.134 / 0.013 / 0.010).

⚠️ Pool selection and all tuning are **in-sample** on 2024-04..2026-09, the same data
reused across H20–H26. No funding, no adverse selection, no bear-market stress. 2026 is
the weakest year (pool10: +18.6%, DD 13.0%).

### 1H ОТВЕРГНУТ (H37, 2026-09-27) — не переигрывать
1H прогнан как **отдельная** гипотеза: тот же harness (`h28.simulate` импортирован
как есть), 7 вариантов, WF 6 окон + MC 3000. Пул 8 монет (ENA/SUI нет 1H-данных),
окно 2024-04..2026-09 — общее с 4H, иначе сравнение нечестное.

| вариант | сделок | expR | PF | доход | DD |
|---------|-------:|-----:|---:|------:|---:|
| 4H без ER | 1625 | 0.170 | 1.485 | +132.6% | 14.8% |
| **4H ER(20) w950 (live)** | 1138 | **0.203** | 1.609 | +103.9% | **11.3%** |
| 1H без ER | 6634 | 0.023 | 1.063 | −15.5% | 43.9% |
| 1H ER(20) w950 | 5425 | 0.025 | 1.068 | −10.8% | 42.2% |
| 1H ER(20) w3800 (time-matched) | 4694 | 0.015 | 1.041 | −22.3% | 40.2% |
| 1H без ER hold120 | 6480 | 0.023 | 1.062 | −16.2% | 46.3% |
| 1H ER950 hold120 | 5333 | 0.024 | 1.065 | −12.2% | 42.0% |

WF OOS (адаптивный, максимально благоприятный для 1H): 4H ER expR 0.218 / PF 1.653
/ 5 из 6 / DD 9.7% против 1H ER expR 0.045 / PF 1.120 / 4 из 6 / **DD 36.2%**.

MC на OOS: 4H ER — доход p5 31.0%, DD p5 5.7%, P(DD>20%) 0.6%. 1H ER — доход
p5 **−14.6%**, DD p95 41.0%, **P(DD>20%) 74.8%**. Единственный circuit breaker
бота — halt на 20% DD, то есть 1H останавливал бы себя в 3 из 4 симуляций.

Три вывода, которые важнее самих цифр:
1. **ER-фильтр не переносится между таймфреймами.** На 4H он даёт +0.033 expR
   (0.170→0.203) и DD 14.8%→11.3%. На 1H — 0.023→0.025, то есть ничего.
2. **Это не издержки.** На 1H издержки съедают 3.86% от 1R против 1.89% на 4H,
   но +2% R не объясняют падение expR на 87%.
3. **Больше сделок ≠ лучше.** 1H даёт 5425 сделок против 1138, и они убыточны.

Прогон воспроизводим: `python h37_1h_experiment.py`, отчёт
`backtest_results/h37_1h_experiment.txt`. **Параметры 1H не крутить** — это была
бы подгонка под тот же ряд, который уже дал in-sample выбор. Если 1H поднимут
снова — это новая гипотерия с новым протоколом, не реанимация H37.

⚠️ В H37 был баг, который чуть не сделал вывод ложным: ключ кэша `prep_tf` не
включал таймфрейм, а набор монет на 4H и 1H совпадает — «1H» молча получал 4H-ряд
и давал IDENTИЧНЫЕ A/B/C/D числа. Сейчас ловит секция 0 (гейт целостности).

### ZDev «расширенный стоп + ER + чистый TP» — ОТВЕРГНУТ (H38, 2026-09-27)
Проверялось буквально то, что предложено: стоп за swing low 12 баров, ER20/950,
TP=eq или 1.61R без трейлинга. `h38_zdev_swing_er.py`, пул 6 монет ZDev,
окно 2023-09..2026-09, WF 6 окон, block-bootstrap 20k. **Ничего не интегрировано.**

Два результата, важнее самих цифр:

1. **Стоп за swing-12 невозможен для входа ZDevby construction.** ZDev входит на
   отклонении 2 ATR от равновесия, то есть на краю движения; prior-экстремум
   за 12 баров лежит по ту же сторону от входа и защитным стопом не является.
   Невалидны **88.5%** сигналов (901 сигнал, 797 не с той стороны).
   Движок (`ab_btc_doji_tvl.py:140-143`) такие сигналы молча отбрасывает —
   поэтому H2 измерял 12% выборки, и его «E=-1.43R» ничего не значил.
   Проверена геометрически замена: стоп = min(фитиль, 2.0×ATR(14)), монотонно
   шире фитиля, всегда валидна.

2. **Расширенный стоп statistically значимо ХУЖЕ, а не лучше.** WR вырос
   (26% → 42-47%), но payoff ratio схлопнулся: expectancy упала.

| арм | сделок | expR | PF | 95% CI | DD |
|-----|-------:|-----:|---:|---------|---:|
| Z0 control ZDev как есть | 516 | −0.073 | 0.91 | [−0.336, +0.251] | 27.2% |
| Z1 Z0 + чистый TP | 514 | +0.035 | 1.03 | [−0.437, +0.629] | 33.7% |
| Z2 Z0 + ER20/950 | 499 | −0.044 | 0.95 | [−0.307, +0.267] | 24.4% |
| Z3 Z0 + TP + ER | 497 | +0.069 | 1.06 | [−0.382, +0.597] | 32.2% |
| Z4 Z0 + стоп 2.0×ATR | 455 | −0.108 | 0.64 | [−0.166, −0.051] | 16.5% |
| Z5 Z0 + стоп + TP + ER | 430 | −0.142 | 0.77 | [−0.244, −0.039] | 20.8% |
| Z6 Z0 + swing-12 (диагност.) | 455 | −0.127 | 0.59 | [−0.186, −0.069] | 19.0% |

ER-фильтр на ZDev не работает: +0.029 expR, p=0.884 (то же, что 1H в H37 — эффект
не переносится). Единственное, что двигаетexpR вверх, — чистый TP (+0.108),
но его CI содержит ноль. Правило окон не работает ни для одной руки:
перестановка сделок внутри монеты даёт 9.8-11.6 окон PF>=1 против наблюдаемых
8-12, то есть расположение сделок по времени не несёт информации.

⚠️ **Отдельно: baseline ZDev в этом файле (PF 3.94 / 2.75 / 3.36 / 2.20 / 2.38 /
3.07) не воспроизводится.** Он получен на `backtest.run_backtest`, где стоп
строится от high/low ТЕКУЩЕГО бара и тут же проверяется его же low/high
(`diag_lookahead.txt`: ADA PF 14.53 против честных 0.59; 86-90% сделок закрыты
таким стопом, давая 107-114% PnL). На аудированном движке Z0 даёт
pooled expR −0.073, PF 0.91, DD 27.2% — **и это выше 20%-го halt бота**.
Самопроверка движка (секция 6 отчёта) сверяет Z0 с аудированным честным
столбцом: ADA 0.58 против 0.59, ARB 1.22 против 1.22, DOGE 0.76, NEAR 0.72.
Вывод: **улучшать нечего — у ZDev нет edge на честном движке.** Таблицу выше
не считать подтверждённой.

Параметры ZDev не крутить: те же данные уже дали in-sample выбор (пул, порог z,
окна WF). Прогон: `python h38_zdev_swing_er.py`.

## INJ под текущим Donchian — НЕ ДОБАВЛЯТЬ (H39, 2026-09-27)
INJ никогда не проверялся под live-стратегией. Старые его метрики (PF 3.38,
+385%) — это ZDev из эпохи lookahead-бага, см. H38; в `h29_candidates.py` его
нет. Прогон: `python h39_inj_check.py`, отчёт
`backtest_results/h39_inj_check.txt`. Данные: `data/raw/INJ_4h_top20.csv`,
9014 баров 2022-08-17..2026-09-27, покрытие эталонного окна 100%.

| монета | сделок | expR | PF | DD | 95% CI на expR |
|--------|-------:|-----:|---:|---:|---------------|
| INJ без ER | 239 | +0.100 | 1.27 | 4.7% | [-0.058, +0.274] |
| INJ с ER (live) | 155 | +0.134 | 1.37 | 3.5% | [-0.071, +0.373] |
| медиана пула | — | +0.162 / +0.185 | — | — | — |

INJ 9-й из 11 без ER и 10-й из 11 с ER — выше только LINK. Не проходит 2 из 4
критериев: CI expR содержит 0, и expR ниже медианы пула. По годам ровный
(2024 +0.028, 2025 +0.116, 2026 +0.153, 3/3 положительных), по половинам
+0.087 / +0.114 — но стабильно слабее пула, а не «в одной половине упал».

Портфель (cap=6): без ER добавление **размежает** пул — expR 0.1690→0.1669,
PF 1.488→1.483, DD 13.0%→13.7%. С ER почти нейтрально: 0.1947→0.1961,
PF 1.589→1.592, DD 11.0%→11.1%. Ни то, ни другое не повод менять пул.

Три вещи, важнее самих цифр:

1. **Движок — `H24.run`, а не `h25_per_coin.trade_r`.** У h25 time-stop считается
   от бара СИГНАЛА (`j - i >= hold`, вход на `i+1`) и срабатывает на бар раньше.
   Live (`core/donchian_breakout.py:296`, `bars_held = bar_idx - bar_idx_входа`)
   и H24 считают от бара ВХОДА. Ошибка одинакова для всех монет, поэтому
   ранжирование в H25/H29 не едет, но абсолютные per-coin цифры там на бар
   короче живых. В H39 pool10 без ER повторяет H26 побитово (1827 / +0.1690 /
   1.488 / 13.0% / 19.8%) — это и есть доказательство, что движок не подменён.
2. **ER-arm расходится с H33/H36 по числу сделок:** 1282 против 1138-1244 при
   том же качестве (PF 1.589 против 1.589-1.609). Причина в H39 не
   расследована; здесь ER fail-closed до 972 баров, как в бою
   (`core/donchian_breakout.py:180`), а H33/H36 считались до фиксации этой
   конвенции. На вердикт по INJ не влияет. **Прежде чем считать ER-arm
   эталонным, конвенции надо согласовать.**
3. **Конкуренция за `cap` путезависима, «портфель ⊆ одиночный» неверно.**
   Отсечённый на баре j вход освобождает монету, и следующий сигнал берётся там,
   где в одиночном прогоне позиция ещё держалась. Проверять утечку состояния
   можно только при `cap=99`: там портфель обязан разложиться на одиночные
   прогоны побитово (в H39 — 10/10 монет, `max|dR| = 0`).

⚠️ Панель обязана быть выровнена ДО `H24.arrays()`: движок индексирует `prep`
позиционно, и монета с внутренними пропусками молча съезжает по времени. Ровно
тот класс бага, что ловил гейт в H37.

## VPS
- **IP:** 161.104.18.192, root — пароль в `SSH_PASSWORD` (в репозитории не хранится)
- **Docker:** `docker exec smc-bot bash`, files in `/app/`
- **Deploy:** SCP to `/opt/smc-fractal-bot/`, then `docker cp` into container
- **Persistent:** Docker container NOT persistent — code changes via `docker cp` only, rebuild needed for permanence

## Secrets
Все секреты передаются через переменные окружения, в репозитории не хранятся:
`BYBIT_API_KEY`, `BYBIT_API_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`DASH_PASS`, `SSH_PASSWORD`. Локально — `.env` (в `.gitignore`), в контейнере —
`docker-compose` env. Бот читает их через `os.getenv` (`main.py:410-440`).

✅ **Bybit API key/secret были захардкожены в `scripts/sync_trades.py`** (коммиты
`257415e`, `122f035`). Файл переведён на `os.getenv`, история переписана
(force-push). **Ротацию ключей владелец 2026-09-27 сознательно отказал делать:
это DEMO-аккаунт, вывести средства нельзя, ущерб ограничен чужими сделками в
демо.** Не переигрывать этот вопрос, если владелец сам не поднимет.

Единственный остаточный риск: если репозиторий станет публичным, старый
демо-ключ можно вытащить из кэша GitHub, форков и по прямым SHA
(`D:\...\repo-backup-before-history-rewrite.bundle` хранит старые объекты
локально). Перед публикацией — ротировать.

- Bybit Demo endpoint: `https://api-demo.bybit.com` (NOT api.bybit.com)
- Telegram: токен и chat_id в `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (канал заблокирован сетью Oracle Cloud, уведомления выключены)

## Strategy (legacy, not live)
- Sweep liquidity → find consolidation center → enter on return
- `tp_mode: 'structure'` — TP at body center of pivot candle (opposite-color before impulse)
- `r_multiple` — fallback if no pivot found (default 1.0R)
- `breakeven_at: 0.3` — move SL to entry after 0.3R profit
- H/L center (`use_body=False`) — validated by 2x2 A/B test: H/L PF=1.41 vs Body PF=0.95

## Legacy ZDev A 2.0 (снят с боевого, остаётся в коде)
```
baseline:     3 / 5.0с
zdev_signal: 0.5R / 4h (1H не берём — PF падает, шум)
strategy:    ZDev A 2.0 = z-score order-flow on 4H only

ПУЛ (6 монет, все zdev, демо):
ADAUSDT  PF 3.94 / +768% / DD 9.2 / WF 6/6
NEARUSDT PF 2.75 / +165% / DD 6.7 / WF 5/6
ARBUSDT  PF 3.36 / +196% / DD 7.6 / WF 4/6
SOLUSDT  PF 2.20 / +130% / DD 6.2 / WF 6/6
DOGEUSDT PF 2.38 / +146% / DD 6.3 / WF 5/6
XRPUSDT  PF 3.07 / +314% / DD 9.8 / WF 4/6
```
Выбыли на этапе скрининга/WF (не в бою): ETH (WF 3/6), SUI (DD 10.5%),
INJ/GRAM/AVAX/GRAM(листинг)/SUI/BTC/ATOM (DD/WF — не прошли), BTC/ETH — SMC больше не используется.

## Key Parameters (config/settings.donchian_pool10.yaml)
```
все 10 монет: lb=20, ATR=20, trail=2.0x, max_bars=30, risk=0.35%, no TP
portfolio:   max_concurrent_positions=6, max_drawdown=20%, commission_haircut=0.0
```
Бот читает **жёстко** `config/settings.yaml` (`load_config()`, `main.py:66`). Чтобы
переключить пул — заменить этот файл (бэкап рядом) либо добавить override пути.

## Architecture
- `main.py` — main loop; `SMCFractalBot`; dispatch по `strategy_type` (smc/zdev/donchian)
- `core/donchian_breakout.py` — DonchianConfig / DonchianSignalGenerator / DonchianExit
  / `position_size` / `DrawdownHalt`. Ядро новой live-стратегии.
- `smc_features.py` — find_pivot_candle, find_structure_tp, find_consolidation_center (H/L default)
- `core/bybit_client.py` — API client, _sync_time before every signed request
- `core/order_executor.py` — maxMktOrderQty, optional TP (None для Donchian)
- `core/position_tracker.py` — trailing stop, time-stop, Donchian state (extreme/atr_entry)
- `core/risk_manager.py` — sizing; `commission_haircut` = 0 для паритета с H24
- `core/redis_store.py` — pipeline atomicity, key prefix `smc:`
- `core/auto_optimizer.py` — PF drop bypasses cooldown
- `core/dashboard.py` — Flask web dashboard on port 80
- `backtest.py` — margin-based, SHORT fixed, commission+slippage, max_leverage=10
- `rolling_optimize.py` — PARAM_RANGES includes tp_mode, structure_tp_lookback
- `verify_donchian_parity.py` — 119 проверок: паритет с H24 + регрессия smc/zdev
  + cap-6 + трейлинг + конфиг + ER-фильтр + **syntax-gate**. Запуск:
  `python verify_donchian_parity.py`
- `h24_unified_cost.py` / `h25_per_coin.py` / `h26_pool_check.py` — портфельные расчёты
- `h32_regime.py` / `h33_er_walkforward.py` / `h36_live_threshold.py` — ER: режимы,
  walk-forward, скользящий порог
- `h34_volume_filter.py` / `h35_funding.py` / `h37_1h_experiment.py` /
  `h38_zdev_swing_er.py` — отклонённые гипотезы (объём, carry, таймфрейм 1H,
  ZDev: расширенный стоп + ER + чистый TP)
- `_er_live_check.py` / `_er_bar_depth_check.py` — проверки на VPS: ER активен в
  боевом конфиге и Bybit отдаёт >= 974 баров на каждый символ пула

## A/B Test Results (ETH, 4H, rolling WF)
| Config | PF | Return | DD |
|--------|-----|--------|-----|
| H/L + structure TP | 1.41 | +29.5% | 9.9% |
| H/L + r_multiple | 1.41 | +29.9% | 9.7% |
| Body + structure TP | 0.95 | -2.2% | 12.6% |
| Body + r_multiple | 0.95 | -2.3% | 12.7% |

## Known Issues
- `leverage not modified (code=110043)` — benign, leverage already 10x
- Dashboard `available` shows 0.0 — Bybit returns empty string for availableToWithdraw on demo
- Local Windows Bybit access geo-blocked (403 Russia) — all Bybit API calls via VPS
- Telegram blocked by Oracle Cloud network policy

## Financials
- Start equity: $171,827 → сброшен рестартом на $166,242.48 (demо-рестарт 2026-09-23, RESTORE)
- Current equity: $166,242.48 (10 монет donchian, демо, старт боевого пула)
- **10 coins live (все donchian):** ADAUSDT, ARBUSDT, DOGEUSDT, ENAUSDT, HBARUSDT,
  LINKUSDT, NEARUSDT, SUIUSDT, XLMUSDT, XRPUSDT
- Risk: статический 0.35% от equity; `dynamic_risk` выключен (H23 провалил prereg)
- Сделки/пары мониторятся через dashboard (http://161.104.18.192, порт 80) и `docker logs smc-bot`

## DO NOT
- Use body center (`use_body=True`) — degrades PF from 1.41 to 0.95
- Use `api.bybit.com` — demo needs `api-demo.bybit.com`
- Run Bybit API calls locally from Windows — geo-blocked
- Use `&&` in SSH commands on Windows PowerShell — use `;` instead
- Trailing ДО `check_exits` — стоп успеет подняться до high текущего бара и закроет
  позицию выше нужного (lookahead, ломает паритет). Проверка `verify_donchian_parity.py`
  ловит это сравнением позиций в `main.py`.
- **Не делать `docker cp` без `python -m py_compile`** — 2026-09-27 сдвинутый отступ
  в баннере уронил контейнер в рестарт-цикл. Сейчас ловит секция 4 в
  `verify_donchian_parity.py`; гонять её **до** любого деплоя.
- Не вставлять секцию `donchian:` внутрь `risk:` — `commission_haircut` «съедается»
  и sizing перестаёт совпадать с H24. Проверяется тестом.
- PowerShell ломает `grep`/`python -c` с `|` и кириллицей в ssh-строках. Для составных
  команд писать скрипт-файл, `scp` + `docker cp` + `docker exec python`.
- `config/` смонтирован волаумом (`./config:/app/config`) — правка на хосте live
  сразу. `main.py` и `core/` вшиты в образ — только `docker cp` + рестарт.
  Бэкап перед деплоем: `bak-YYYYmmdd-HHMMSS/` в `/opt/smc-fractal-bot`.
