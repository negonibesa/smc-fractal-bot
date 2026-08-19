# SMC Fractal Bot

Автономный торговый бот на базе Smart Money Concepts (SMC). Deploy and forget.

## Стратегия

**Sweep → Return to Center → Enter OPPOSITE**

1. Обнаружить liquidity sweep на 4H таймфрейме
2. Дождаться возврат цены к центру консолидации (фиксированному в момент свипа)
3. Войти ПРОТИВ направления свипа
4. "Перед импульсом цена всегда приходит к равновесию"

## Статус

**Рабочий автономный бот на Bybit Testnet.**

- ✅ Bybit V5 API (testnet)
- ✅ Redis persistence (позиции, сделки, статистика, signal state)
- ✅ Telegram уведомления + команды (/help)
- ✅ Multi-pair: BNBUSDT, RENDERUSDT, SOLUSDT, LINKUSDT
- ✅ Walk-forward validated (ROBUST)
- ✅ Автооптимизация v2 (walk-forward + guard rails + regime params)
- ✅ Regime detector (bull/bear/sideways + adaptive params)
- ✅ Per-pair фильтры (session, 1D trend)
- ✅ Daily + Weekly reports (Telegram, auto at 00:10 UTC)
- ✅ Stress tests (1H, 4H, 1D, bear periods)
- ⬜ Деплой на VPS (Docker)

## Telegram Commands

| Команда | Описание |
|---------|----------|
| `/help` | Список всех команд |
| `/status` | Equity, regime, позиции, rolling PF |
| `/balance` | Equity, available, used, открытые позиции |
| `/report` | То же что /status |
| `/daily` | Дневной отчёт (PnL, DD, rolling PF, regime) |
| `/weekly` | Недельный отчёт (PF, WR, avg R, per-pair, long/short) |

### Автоматические уведомления

- ENTRY — вход (направление, цена, SL, TP, размер)
- EXIT — закрытие (причина, PnL)
- DAILY REPORT — дневная статистика (00:10 UTC)
- WEEKLY REPORT — недельная статистика (воскресенье 00:10 UTC)
- AUTO-OPTIMIZE — уведомление при оптимизации
- ERROR — ошибки API

## Архитектура

```
smc_fractal_bot/
├── config/
│   ├── settings.yaml          # Multi-pair конфиг + фильтры
│   ├── best.yaml              # Legacy best config
│   └── optimal_v2.yaml        # Iron baseline (NEVER auto-overwrite)
├── core/
│   ├── bybit_client.py        # Bybit V5 API клиент
│   ├── order_executor.py      # Исполнение ордеров
│   ├── position_tracker.py    # Трекинг позиций + Redis
│   ├── risk_manager.py        # Риск-менеджмент + Redis
│   ├── redis_store.py         # Redis persistence
│   ├── notifier.py            # Telegram уведомления
│   ├── auto_optimizer.py      # Walk-forward оптимизатор v2
│   ├── regime_detector.py     # Bull/bear/sideways detection
│   └── reporter.py            # Daily/Weekly reports + metrics
├── data/raw/                  # CSV данные (4H, 1H, 1D)
├── logs/
│   └── bot.log                # Основной лог
├── main.py                    # Основной цикл бота
├── backtest.py                # Бэктест движок
├── smc_features.py            # SMC детекция (sweep, center, ADX)
├── test_v2.py                 # Walk-forward тест v2
├── test_final.py              # Baseline vs optimal comparison
├── test_stress.py             # Stress tests (timeframes + bear)
├── requirements.txt           # Зависимости
└── .env                       # API ключи (не в git)
```

## Установка

```bash
pip install -r requirements.txt
# Настроить .env (BYBIT_API_KEY, BYBIT_API_SECRET, TELEGRAM_*)
python test_connection.py    # Bybit
python test_redis.py         # Redis
```

## Запуск

```bash
# Запустить Redis (если не запущен)
Start-Process "C:\Program Files\Redis\redis-server.exe" -WindowStyle Hidden

# Запустить бота
python main.py
```

## Результаты (Walk-Forward + 4-Year Backtest)

### Walk-Forward Validation (OOS)

| Pair | OOS PF | OOS Return | Вердикт | Фильтры |
|------|--------|-----------|---------|---------|
| BNBUSDT | **4.87** | +8.0% | ROBUST | ADX only |
| RENDERUSDT | **2.76** | +13.1% | ROBUST | +session (8-21 UTC) |

### 4-Year Backtest (max_leverage=20, dynamic risk)

| Pair | Period | Trades | WR | PF | Annual | Max DD |
|------|--------|--------|-----|------|--------|--------|
| BNBUSDT | 44 mo | 337 | 40% | 2.27 | +20.3% | 3.50% |
| RENDERUSDT | 25 mo | 146 | 48% | 9.34 | +47.5% | 4.06% |
| SOLUSDT | 44 mo | 453 | 40% | 2.22 | +19.1% | 5.46% |
| LINKUSDT | 44 mo | 425 | 42% | 3.09 | +31.1% | 5.27% |

### Per-pair конфигурация

| Параметр | BNB | RENDER | SOL | LINK |
|----------|-----|--------|-----|------|
| lookback | 12 | 12 | 12 | 12 |
| sweep_threshold | 0.008 | 0.008 | 0.008 | 0.008 |
| center_proximity | 0.012 | 0.012 | 0.012 | 0.012 |
| tp_multiplier | 1.0 | 1.0 | 1.0 | 1.0 |
| timeout | 15 | 15 | 15 | 15 |
| adx_filter | ✅ 25 | ✅ 25 | ✅ 25 | ✅ 25 |
| session_filter | — | ✅ 8-21 UTC | — | — |
| trend_1d_filter | — | — | — | — |

## Stress Test Results

| Pair | TF | PF | WR | Return | DD | Trades |
|------|-----|-----|-----|--------|-----|--------|
| BNB | 1H | 1.28 | 41% | +3.4% | 2.85% | 54 |
| BNB | **4H** | **4.53** | **70%** | **+27.1%** | 1.85% | 46 |
| BNB | 1D | 8.28 | 70% | +8.4% | 1.03% | 10 |
| RENDER | **4H** | **2.67** | **46%** | **+39.1%** | 3.17% | 102 |
| RENDER | 1D | 6.13 | 62% | +10.6% | 1.01% | 8 |

**Bear market**: RENDER PF=18.87 (5 trades) — стратегия выживает в падениях.

**Вывод**: 4H = оптимальный ТФ для обеих пар.

## Автооптимизация v2

**Deploy and forget** — бот сам следит за качеством и пересчитывает параметры.

### Триггеры

| Триггер | Условие | Действие |
|---------|---------|----------|
| PF drop | PF < 1.2 после 20+ сделок | Walk-forward оптимизация |
| Time | 30+ дней без оптимизации | Walk-forward оптимизация |
| Max DD | Daily DD > 5% | Немедленная оптимизация |

### Защитные механизмы

- Walk-forward валидация перед применением
- `optimal_v2.yaml` никогда не перезаписывается
- Param delta < 50% от baseline
- Min 10% improvement required
- 7-day cooldown после оптимизации
- Trade count drop < 50% OK если PF улучшается
- ADX НИКОГДА не оптимизируется (часть стратегии)
- Trailing params оптимизируются (breakeven_at, trail_activate, trail_step)

### Regime Detection

| Режим | Params | Логика |
|-------|--------|--------|
| Bull 🟢 | wider entry, tp×1.5 | EMA20>EMA50, ADX>25 |
| Bear 🔴 | tighter entry, faster TP | EMA20<EMA50, ADX>25 |
| Sideways 🟡 | baseline defaults | ADX<25 |

### Per-pair фильтры

- **Session filter** (RENDER): вход только 8-21 UTC (Лондон+Нью-Йорк)
- **1D trend filter** (FIL): не торговать против дневного тренда (EMA50)
- **Volume filter**: ОТКЛОНЁН (слишком агрессивен)

## Reports (Grok Spec)

### Daily Report (00:10 UTC)

- Equity, Daily PnL, Trades today (W/L)
- Current Drawdown, Rolling PF(20) + status (🟢>1.6 / 🟡>1.3 / 🔴<1.3)
- Open positions, Regime per pair, Last optimization

### Weekly Report (Воскресенье 00:10 UTC)

- Weekly Return, Win Rate, Trades (W/L)
- Rolling PF(30), Rolling WR(30), Avg R
- Max DD (week + total), Avg trade duration, Time in position %
- Per pair: PF, WR, trades, PnL
- Long vs Short: trades, WR, PnL
- Regime per pair
- Optimization count + param changes

## Redis Persistence

```
smc:pos:{symbol}        — открытые позиции
smc:trades:closed       — история закрытых сделок (с exit_price, exit_reason, size)
smc:risk                — статистика риска
smc:signal:{symbol}     — состояние signal generator
```

При перезапуске всё восстанавливается автоматически.

## Тестирование

```bash
python test_connection.py    # Bybit connection
python test_redis.py         # Redis persistence
python test_v2.py baseline   # Walk-forward baseline
python test_v2.py session    # Session filter test
python test_final.py         # Baseline vs optimal comparison
python test_stress.py        # Stress tests (timeframes + bear)
python screen_pairs.py       # Pair screening
```

## Зависимости

```
requests>=2.31.0
pandas>=2.0.0
numpy>=1.24.0
PyYAML>=6.0
python-dotenv>=1.0.0
redis>=4.0.0,<5.0.0
matplotlib>=3.7.0
```

## Безопасность

- API ключи в `.env` (не в git)
- Testnet по умолчанию
- Risk limits: 1% на сделку, max drawdown 10%
- `optimal_v2.yaml` — iron baseline, never auto-overwrite
