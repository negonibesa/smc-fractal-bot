# SMC Fractal Bot — Project Context

## What
SMC fractal trading bot for Bybit Demo Trading. 4H candles, sweep→center→return logic. Production strategy is **ZDev A 2.0** (z-score order-flow entry, not SMC). 6 coins live: ADAUSDT, NEARUSDT, ARBUSDT, SOLUSDT, DOGEUSDT, XRPUSDT.

## VPS
- **IP:** 161.104.18.192, root, pass `gC8Bk6zHdgPGa5hT`
- **Docker:** `docker exec smc-bot bash`, files in `/app/`
- **Deploy:** SCP to `/opt/smc-fractal-bot/`, then `docker cp` into container
- **Persistent:** Docker container NOT persistent — code changes via `docker cp` only, rebuild needed for permanence

## API Keys
- Bybit Demo: `XSuuulrhlNGtVd4zYI` / `nLa1diavcn7IqJWTugJL23eKeXqYXVM6f4fN`
- Endpoint: `https://api-demo.bybit.com` (NOT api.bybit.com)
- Telegram: bot `8372883117:AAGa7tg8LYmWYDG5Dj-7VGFFoazxFQSzoEA`, chat `266366821` (blocked by Oracle Cloud network)

## Strategy
- Sweep liquidity → find consolidation center → enter on return
- `tp_mode: 'structure'` — TP at body center of pivot candle (opposite-color before impulse)
- `r_multiple` — fallback if no pivot found (default 1.0R)
- `breakeven_at: 0.3` — move SL to entry after 0.3R profit
- H/L center (`use_body=False`) — validated by 2x2 A/B test: H/L PF=1.41 vs Body PF=0.95

## Live Config → ZDev A 2.0 (одно «окно», все 6 монет)
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

## Key Parameters (settings.yaml)
```
ADAUSDT: lb=12, sw=0.008, prox=0.012, tp=1.0, timeout=15, adx=25
NEARUSDT: lb=12, sw=0.008, prox=0.012, tp=1.0, adx=25
ARBUSDT: lb=12, sw=0.008, prox=0.012, tp=1.0, adx=25
SOLUSDT: lb=12, sw=0.008, prox=0.012, tp=1.0, adx=25
DOGEUSDT: lb=12, sw=0.008, prox=0.012, tp=1.0, adx=25
XRPUSDT: lb=12, sw=0.008, prox=0.012, tp=1.0, adx=25
```

## Architecture
- `main.py` — main loop, SignalGenerator with tp_mode support
- `smc_features.py` — find_pivot_candle, find_structure_tp, find_consolidation_center (H/L default)
- `core/bybit_client.py` — API client, _sync_time before every signed request
- `core/order_executor.py` — maxMktOrderQty for market orders
- `core/position_tracker.py` — trailing stop logic
- `core/risk_manager.py` — dynamic risk, max_consecutive_losses resets on win OR day change
- `core/redis_store.py` — pipeline atomicity, key prefix `smc:`
- `core/auto_optimizer.py` — PF drop bypasses cooldown
- `core/dashboard.py` — Flask web dashboard on port 80
- `backtest.py` — margin-based, SHORT fixed, commission+slippage, max_leverage=10
- `rolling_optimize.py` — PARAM_RANGES includes tp_mode, structure_tp_lookback

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
- Current equity: $166,242.48 (6 монет zdev, демо, старт боевого пула)
- **6 coins live (все zdev):** ADAUSDT, NEARUSDT, ARBUSDT, SOLUSDT, DOGEUSDT, XRPUSDT
- Dynamic risk: base_risk=1.5 (overrides risk_percent=1.0)
- Сделки/пары мониторятся через dashboard (http://161.104.18.192, порт 80) и `docker logs smc-bot`

## DO NOT
- Use body center (`use_body=True`) — degrades PF from 1.41 to 0.95
- Use `api.bybit.com` — demo needs `api-demo.bybit.com`
- Run Bybit API calls locally from Windows — geo-blocked
- Use `&&` in SSH commands on Windows PowerShell — use `;` instead
