# SMC Fractal Bot — Project Context

## What
SMC (Smart Money Concepts) fractal trading bot for Bybit Demo Trading. 4H candles, sweep→center→return logic, 2 coins live: ETHUSDT, GRAMUSDT.

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

## Key Parameters (settings.yaml)
```
ETHUSDT: lb=12, sw=0.005, prox=0.015, tp=1.0
GRAMUSDT: lb=12, sw=0.008, prox=0.012, tp=1.0
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
- Start equity: $171,827 (real demo start)
- Current equity: ~$168,605 (41 buggy trades lost $2,867 early on)
- 2 coins live: ETHUSDT (bull), GRAMUSDT (bull)
- Dynamic risk: base_risk=1.5 (overrides risk_percent=1.0)

## DO NOT
- Use body center (`use_body=True`) — degrades PF from 1.41 to 0.95
- Use `api.bybit.com` — demo needs `api-demo.bybit.com`
- Run Bybit API calls locally from Windows — geo-blocked
- Use `&&` in SSH commands on Windows PowerShell — use `;` instead
