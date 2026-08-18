# SMC Fractal Bot

Автономный торговый бот на базе Smart Money Concepts (SMC).

## Стратегия

**Sweep → Return to Center → Enter OPPOSITE**

1. Обнаружить liquidity sweep на 4H таймфрейме
2. Дождаться возврат цены к центру консолидации
3. Войти ПРОТИВ направления свипа
4. "Перед импульсом цена всегда приходит к равновесию"

## Текущий статус (сентябрь 2026)

**Рабочий бот на Bybit Testnet с Redis persistence и Telegram уведомлениями.**

- ✅ Bybit V5 API подключено (testnet)
- ✅ Redis persistence (позиции, сделки, статистика)
- ✅ Telegram уведомления (вход/выход/ошибки/дневной отчёт)
- ✅ Multi-pair: BNBUSDT, RENDERUSDT, FILUSDT
- ✅ Walk-forward анализ пройден
- ⬜ Деплой на VPS
- ⬜ Автооптимизация по триггерам

## Архитектура

```
smc_fractal_bot/
├── config/
│   ├── settings.yaml          # Все настройки + per-pair конфиги
│   └── best.yaml              # Лучший конфиг (PF=4.53)
├── core/
│   ├── __init__.py
│   ├── bybit_client.py        # Bybit V5 API клиент
│   ├── order_executor.py      # Исполнение ордеров
│   ├── position_tracker.py    # Трекинг позиций + Redis
│   ├── risk_manager.py        # Риск-менеджмент + Redis
│   ├── redis_store.py         # Redis persistence
│   └── notifier.py            # Telegram уведомления
├── data/raw/                  # CSV данные (27 пар, 4H)
├── logs/                      # Логи, графики, отчёты
├── main.py                    # Основной цикл бота
├── backtest.py                # Бэктест движок
├── auto_optimize.py           # Автооптимизатор
├── smc_features.py            # SMC детекция (sweep, center, ADX)
├── data_loader.py             # Bybit API загрузчик данных
├── requirements.txt           # Зависимости
└── .env                       # API ключи (не в git)
```

## Установка

```bash
# 1. Установить зависимости
pip install -r requirements.txt

# 2. Установить Redis 3.0+ (уже установлен)
# Или через Docker:
# docker run -d --name redis -p 6379:6379 redis:7

# 3. Настроить .env
cp .env.example .env
# Заполнить BYBIT_API_KEY, BYBIT_API_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# 4. Проверить подключение
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

## Конфигурация (config/settings.yaml)

### Multi-pair конфиг

Каждая пара имеет свой набор параметров:

```yaml
assets:
  - symbol: BNBUSDT
    enabled: true
    config:
      strategy:
        lookback: 12
        sweep_threshold: 0.008
        center_proximity: 0.012
        tp_multiplier: 1.0
        timeout: 15
      filters:
        adx_filter:
          enabled: true
          min_adx: 25
      trailing:
        enabled: true
        breakeven_at: 0.5
        trail_activate: 1.0
        trail_step: 0.5
```

### Параметры стратегии

| Параметр | Описание | BNB | RENDER | FIL |
|----------|----------|-----|--------|-----|
| lookback | Свечей для центра | 12 | 12 | 12 |
| sweep_threshold | Порог свипа | 0.008 | 0.008 | 0.008 |
| center_proximity | Возврат к центру | 0.012 | 0.012 | 0.012 |
| tp_multiplier | Множитель тейка | 1.0 | 1.0 | 1.0 |
| timeout | Таймаут (свечи) | 15 | 15 | 15 |

### Триггеры автооптимизации

```yaml
optimizer:
  trigger_days: 30        # Каждые 30 дней
  trigger_trades: 20      # Или после 20 сделок
  trigger_pf_drop: 1.2    # Или если PF < 1.2
  min_improvement: 0.10   # Новый конфиг должен быть на 10% лучше
```

## Результаты бэктеста

### BNBUSDT (12 месяцев)
| Метрика | Значение |
|---------|----------|
| Profit Factor | **4.53** |
| Win Rate | **70%** |
| Return | **+27.09%** |
| Max Drawdown | **1.85%** |
| Trades | 46 |
| Sharpe | 8.37 |

### Walk-Forward Analysis (переоптимизация)
| Период | PF | Return | Вердикт |
|--------|-----|--------|---------|
| FULL | 4.53 | +27.1% | - |
| FIRST 50% (train) | 4.93 | +17.1% | - |
| SECOND 50% (test) | 4.00 | +10.4% | ROBUST |
| LAST 30% (recent) | 5.74 | +5.6% | ROBUST |

**Вердикт: НЕ переоптимизирован.** Out-of-sample PF = 4.87.

### RENDERUSDT (14 месяцев)
| PF | WR | Return | DD | Trades |
|-----|-----|--------|-----|--------|
| 2.67 | 46% | +39.1% | 3.17% | 102 |
| OOS PF: **3.39** | OOS Ret: **+25.2%** | **ROBUST** |

### FILUSDT (14 месяцев)
| PF | WR | Return | DD | Trades |
|-----|-----|--------|-----|--------|
| 2.64 | 46% | +38.5% | 2.48% | 122 |
| OOS PF: **1.56** | OOS Ret: **+7.0%** | **GOOD** |

## Redis Persistence

Бот хранит в Redis:
- Открытые позиции
- История сделок (до 1000)
- Статистика риска (equity, дневные лимиты)
- Состояние signal generator

При перезапуске всё восстанавливается автоматически.

### Ключи Redis
```
smc:pos:{symbol}        — открытые позиции
smc:pos:index           — множество символов с позициями
smc:trades:closed       — история закрытых сделок
smc:risk                — статистика риска
smc:signal:{symbol}     — состояние signal generator
```

## Telegram уведомления

Бот отправляет:
- **ENTRY** — вход в позицию (направление, цена, SL, TP, размер)
- **EXIT** — закрытие (причина, PnL)
- **DAILY REPORT** — дневная статистика
- **ERROR** — ошибки API
- **START/STOP** — старт и остановка

## Тестирование

```bash
# Подключение к Bybit
python test_connection.py

# Подключение к Redis
python test_redis.py

# Тест ордера (testnet)
python test_order.py

# Walk-forward анализ
python test_overfit.py

# Скрининг всех пар
python screen_pairs.py
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

- API ключи хранятся в `.env` (не в git)
- Redis без пароля (localhost only)
- Testnet режим по умолчанию
- Risk limits: 1% на сделку, max drawdown 10%

## Следующие шаги

1. **Деплой на VPS** — Linux сервер с Docker
2. **Redis в Docker** — заменить старый Redis 3.0
3. **Автооптимизация** — триггеры PF < 1.2
4. **Multi-timeframe** — добавить 1H для входов
5. **Дополнительные пары** — XRP, BTC, ETH
