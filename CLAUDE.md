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
- `h34_volume_filter.py` / `h35_funding.py` — отклонённые гипотезы (объём, carry)
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
