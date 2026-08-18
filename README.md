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
- ✅ Telegram уведомления (вход/выход/ошибки/дневной отчёт/оптимизация)
- ✅ Multi-pair: BNBUSDT, RENDERUSDT (FILUSDT on mainnet)
- ✅ Walk-forward validated (ROBUST)
- ✅ Автооптимизация v2 (walk-forward + guard rails)
- ✅ Regime detector (bull/bear/sideways + adaptive params)
- ✅ Per-pair фильтры (session, 1D trend)
- ⬜ Деплой на VPS (Docker)

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
│   └── regime_detector.py     # Bull/bear/sideways detection
├── data/raw/                  # CSV данные (4H, 1H, 1D)
├── logs/
│   ├── bot.log                # Основной лог
│   └── optimizer.log          # Лог оптимизаций
├── main.py                    # Основной цикл бота
├── backtest.py                # Бэктест движок
├── smc_features.py            # SMC детекция (sweep, center, ADX)
├── test_v2.py                 # Walk-forward тест v2
├── test_final.py              # Сравнение baseline vs optimal
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

## Результаты (Walk-Forward Validated)

### Оптимальный конфиг (v2)

| Pair | OOS PF | OOS Return | Вердикт | Фильтры |
|------|--------|-----------|---------|---------|
| BNBUSDT | **4.87** | +8.0% | ROBUST | ADX only |
| RENDERUSDT | **2.76** | +13.1% | ROBUST | +session (8-21 UTC) |
| FILUSDT | **1.43** | +4.9% | GOOD | +1D trend EMA50 |

### Per-pair конфигурация

| Параметр | BNB | RENDER | FIL |
|----------|-----|--------|-----|
| lookback | 12 | 12 | 12 |
| sweep_threshold | 0.008 | 0.008 | 0.008 |
| center_proximity | 0.012 | 0.012 | 0.012 |
| tp_multiplier | 1.0 | 1.0 | 1.0 |
| timeout | 15 | 15 | 15 |
| adx_filter | ✅ 25 | ✅ 25 | ✅ 25 |
| session_filter | — | ✅ 8-21 UTC | — |
| trend_1d_filter | — | — | ✅ EMA50 |

### Walk-Forward Results

```
  BNB (2200 candles):   OOS PF=4.87  Ret=+8%   ROBUST
  RENDER (4522 candles): OOS PF=2.76  Ret=+13%  ROBUST
  FIL (5000 candles):    OOS PF=1.43  Ret=+5%   GOOD
```

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
| Bull | wider entry, tp×1.5 | EMA20>EMA50, ADX>25 |
| Bear | tighter entry, faster TP | EMA20<EMA50, ADX>25 |
| Sideways | baseline defaults | ADX<25 |

### Per-pair фильтры

- **Session filter** (RENDER): вход только 8-21 UTC (Лондон+Нью-Йорк)
- **1D trend filter** (FIL): не торговать против дневного тренда (EMA50)
- **Volume filter**: ОТКЛОНЁН (слишком агрессивен)

## Redis Persistence

```
smc:pos:{symbol}        — открытые позиции
smc:trades:closed       — история закрытых сделок
smc:risk                — статистика риска
smc:signal:{symbol}     — состояние signal generator
```

При перезапуске всё восстанавливается автоматически.

## Telegram

- ENTRY — вход (направление, цена, SL, TP, размер)
- EXIT — закрытие (причина, PnL)
- DAILY REPORT — дневная статистика
- AUTO-OPTIMIZE — уведомление при оптимизации
- ERROR — ошибки API

## Тестирование

```bash
python test_connection.py    # Bybit connection
python test_redis.py         # Redis persistence
python test_order.py         # Live order (testnet)
python test_v2.py baseline   # Walk-forward baseline
python test_v2.py fixed_center  # Fixed center test
python test_v2.py session    # Session filter test
python test_final.py         # Baseline vs optimal comparison
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
