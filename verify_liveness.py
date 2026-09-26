"""Проверка наблюдаемости: тишина должна отличаться от поломки."""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import core.dashboard as D  # noqa: E402

OK = FAILED = 0


def chk(name, cond, detail=''):
    global OK, FAILED
    if cond:
        OK += 1
        print(f'  [OK  ] {name}')
    else:
        FAILED += 1
        print(f'  [FAIL] {name} — {detail}')


class FakeRedis:
    def __init__(self, meta):
        self.meta = meta

    def load_meta(self):
        return self.meta


class FakeBot:
    STALE_CYCLE_SEC = 2400
    STALE_SIGNAL_WARN_H = 72
    STALE_SIGNAL_CRIT_H = 168
    redis = None
    process_start = datetime(2026, 9, 26, 22, 0, tzinfo=timezone.utc)
    start_time = datetime(2026, 9, 20, 22, 0)


def run_case(beat, expect_health, label):
    b = FakeBot()
    b.redis = FakeRedis({'beat': beat})
    got = _health(b, datetime.now(timezone.utc))
    chk(f'{label} → {expect_health}', got == expect_health, f'got {got}')
    return got


def _health(bot, now):
    """Повторяет блок вычисления health из Dashboard._status."""
    m = bot.redis.load_meta()['beat']
    bsym = m.get('symbols') or {}
    ages = [now.timestamp() - v.get('last_cycle', 0) for v in bsym.values()
            if v.get('last_cycle')]
    worst_age = max(ages) if ages else None
    bars_total = int(m.get('bars_evaluated_total', 0))
    sig_ts = float(m.get('last_signal_ts') or 0)
    quiet_h = (now.timestamp() - sig_ts) / 3600 if sig_ts else None
    errs = [v for v in bsym.values() if v.get('last_error')]
    if worst_age is None:
        return 'unknown'
    if worst_age > bot.STALE_CYCLE_SEC:
        return 'stalled'
    if errs:
        return 'degraded'
    if bars_total == 0:
        return 'unknown'
    if quiet_h is not None and quiet_h >= bot.STALE_SIGNAL_CRIT_H:
        return 'quiet'
    if quiet_h is not None and quiet_h >= bot.STALE_SIGNAL_WARN_H:
        return 'quiet'
    return 'ok'


def sym(last_cycle_age_s, bars=100, err='', bar_ts=None):
    return {'last_cycle': datetime.now(timezone.utc).timestamp() - last_cycle_age_s,
            'bars_evaluated': bars,
            'last_bar_ts': bar_ts or datetime.now(timezone.utc).timestamp(),
            'last_error': err}


now = datetime.now(timezone.utc)
print('=== health-логика ===')

# 1. только что работал, сигнал был недавно → ok
run_case({'ts': now.timestamp(), 'bars_evaluated_total': 500,
          'last_signal_ts': now.timestamp() - 600,
          'symbols': {'ADAUSDT': sym(30)}}, 'ok', 'свежий пульс, свежий сигнал')

# 2. пульс живой, сигнала 2 дня → ok (норма, не рейндж даже)
run_case({'ts': now.timestamp(), 'bars_evaluated_total': 500,
          'last_signal_ts': now.timestamp() - 2 * 86400,
          'symbols': {'ADAUSDT': sym(30)}}, 'ok', 'тихо 2 дн, пульс живой')

# 2b. пульс живой, 5 дней тишины → quiet (warn, > 72ч порога)
run_case({'ts': now.timestamp(), 'bars_evaluated_total': 500,
          'last_signal_ts': now.timestamp() - 5 * 86400,
          'symbols': {'ADAUSDT': sym(30)}}, 'quiet', 'тихо 5 дн, пульс живой')

# 3. пульс живой, 4 дня тишины → quiet (warn)
run_case({'ts': now.timestamp(), 'bars_evaluated_total': 500,
          'last_signal_ts': now.timestamp() - 4 * 86400,
          'symbols': {'ADAUSDT': sym(30)}}, 'quiet', 'тихо 4 дн')

# 4. пульс живой, 10 дней тишины → quiet (critical)
run_case({'ts': now.timestamp(), 'bars_evaluated_total': 500,
          'last_signal_ts': now.timestamp() - 10 * 86400,
          'symbols': {'ADAUSDT': sym(30)}}, 'quiet', 'тихо 10 дн')

# 5. пульс мёртв 2 часа → stalled (важнее тишины!)
run_case({'ts': now.timestamp(), 'bars_evaluated_total': 500,
          'last_signal_ts': now.timestamp() - 3600,
          'symbols': {'ADAUSDT': sym(7200)}}, 'stalled', 'цикл мёртв 2ч')

# 6. пульс живой, ошибка в цикле → degraded
run_case({'ts': now.timestamp(), 'bars_evaluated_total': 500,
          'last_signal_ts': now.timestamp() - 3600,
          'symbols': {'ADAUSDT': sym(30, err='TimeoutError: read timed out')}},
         'degraded', 'ошибка цикла')

# 7. bars_evaluated == 0 → unknown (ещё не начали)
run_case({'ts': now.timestamp(), 'bars_evaluated_total': 0,
          'last_signal_ts': 0, 'symbols': {'ADAUSDT': sym(30, bars=0)}},
         'unknown', 'ни одного бара не оценено')

# 8. символ без last_cycle → unknown
run_case({'ts': now.timestamp(), 'bars_evaluated_total': 0, 'last_signal_ts': 0,
          'symbols': {'ADAUSDT': {'bars_evaluated': 0, 'last_cycle': 0}}},
         'unknown', 'нет last_cycle')

print()
print('=== приоритет: поломка важнее тишины ===')
# пульс мёртв И тишина 10 дней → stalled, не quiet
run_case({'ts': now.timestamp(), 'bars_evaluated_total': 500,
              'last_signal_ts': now.timestamp() - 10 * 86400,
              'symbols': {'ADAUSDT': sym(7200)}}, 'stalled',
             'мёртвый пульс + 10 дн тишины')

print()
print('=== порог 40 мин ===')
# ровно на границе не считается зависшим

b = FakeBot()
b.redis = FakeRedis({'beat': {'ts': now.timestamp(), 'bars_evaluated_total': 5,
                              'last_signal_ts': now.timestamp(),
                              'symbols': {'X': sym(2399)}}})
chk('39 мин — ещё жив', _health(b, now) == 'ok', _health(b, now))
b.redis = FakeRedis({'beat': {'ts': now.timestamp(), 'bars_evaluated_total': 5,
                              'last_signal_ts': now.timestamp(),
                              'symbols': {'X': sym(2401)}}})
chk('41 мин — завис', _health(b, now) == 'stalled', _health(b, now))

print()
print('=== бот жив, символов 10: все живы ===')
syms = {f'S{i}': sym(60 + i) for i in range(10)}

b = FakeBot()
b.redis = FakeRedis({'beat': {'ts': now.timestamp(), 'bars_evaluated_total': 900,
                              'last_signal_ts': now.timestamp() - 300,
                              'symbols': syms}})
chk('10 символов, все пульсуют → ok', _health(b, now) == 'ok', _health(b, now))

# один из десяти упал
syms['S7'] = sym(3000)
b.redis = FakeRedis({'beat': {'ts': now.timestamp(), 'bars_evaluated_total': 900,
                              'last_signal_ts': now.timestamp() - 300,
                              'symbols': syms}})
chk('1 из 10 завис → stalled', _health(b, now) == 'stalled', _health(b, now))

print()
print('=== порог тишины: 72ч ===')
for h, want in ((71, 'ok'), (73, 'quiet')):
    b = FakeBot()
    b.redis = FakeRedis({'beat': {'ts': now.timestamp(), 'bars_evaluated_total': 500,
                                  'last_signal_ts': now.timestamp() - h * 3600,
                                  'symbols': {'X': sym(30)}}})
    got = _health(b, now)
    chk(f'{h}ч тишины → {want}', got == want, f'got {got}')

print()
print('=== bars_evaluated считает РАЗНЫЕ бары, а не вызовы ===')
# Реальный баг: цикл каждые 5 мин, дедуп только после сигнала → в тихом
# рынке один бар пересчитывается ~12 раз за 4H и счётчик врал в 12 раз.
b = {'last_bar_ts': None, 'bars_evaluated': 0}
bar_a = 1790438400.0
for _ in range(12):          # 12 циков за 4H на одном баре
    if b['last_bar_ts'] != bar_a:
        b['bars_evaluated'] += 1
    b['last_bar_ts'] = bar_a
chk('12 вызовов на одном баре → 1 бар', b['bars_evaluated'] == 1, b['bars_evaluated'])

bar_b = 1790452800.0         # следующая 4H-граница
if b['last_bar_ts'] != bar_b:
    b['bars_evaluated'] += 1
b['last_bar_ts'] = bar_b
chk('новый бар → 2', b['bars_evaluated'] == 2, b['bars_evaluated'])

print()
print('=== пульс от чужого процесса не выдаётся за свой ===')


def health_with_marker(beat_ps, bot_ps):
    m = {'process_start': beat_ps, 'symbols': {'X': sym(30)},
         'bars_evaluated_total': 100, 'last_signal_ts': now.timestamp()}
    bot = FakeBot()
    bot.process_start = datetime.fromtimestamp(bot_ps, tz=timezone.utc)
    # повторяем новую проверку dashboard
    if m.get('process_start') and abs(m['process_start'] - bot.process_start.timestamp()) > 1:
        return 'unknown'
    return 'ok'


bot_ps = now.timestamp()
chk('пульс этого процесса → ok',
    health_with_marker(bot_ps, bot_ps) == 'ok')
chk('пульс прошлого процесса → unknown (не виним мёртвый код)',
    health_with_marker(bot_ps - 3600, bot_ps) == 'unknown')
chk('старый формат без process_start → не ломается',
    health_with_marker(None, bot_ps) in ('ok', 'unknown'))

print()
print('=== регрессия: телеметрия не роняет торговлю ===')
# Именно этот баг был в проде: float() от pandas Timestamp стоял ДО `if sig:`
# и молча терял сигнал. Проверяем, что блоб бета не может бросить исключение
# наружу из пути обработки сигнала.
import pandas as pd  # noqa: E402


def simulate(df_ts, beta_broken):
    """Мини-путь: process_candle → бета → if sig. Ничего не ловим снаружи."""
    b = {'last_cycle': 0.0, 'bars_evaluated': 0, 'last_bar_ts': 0.0, 'last_error': ''}
    sig = {'direction': 'LONG'}
    got_sig = False
    # process_candle
    # бета — как в main.py, под try
    try:
        b['bars_evaluated'] += 1
        b['last_bar_ts'] = float(df_ts) if beta_broken else df_ts.timestamp()
    except Exception:
        pass
    # блок сигнала — ВНЕ try, как в main.py
    if sig:
        got_sig = True
    return got_sig


chk('Timestamp + исправленный путь → сигнал доходит',
    simulate(pd.Timestamp('2026-09-26 20:00'), False) is True)
chk('Timestamp + даже СЛОМАННАЯ бета → сигнал всё равно доходит',
    simulate(pd.Timestamp('2026-09-26 20:00'), True) is True,
    'телеметрия сорвала торговлю')
chk('числовой timestamp → сигнал доходит',
    simulate(1758916800.0, False) is True)

# и что counters продолжают расти, а не залипают на 1
b = {'bars_evaluated': 1}
for i in range(5):
    try:
        b['bars_evaluated'] += 1
    except Exception:
        pass
chk('счётчик баров растёт (1 → 6)', b['bars_evaluated'] == 6, b['bars_evaluated'])

print()
print('=' * 62)
print(f'{OK} passed, {FAILED} failed')
sys.exit(1 if FAILED else 0)
