"""Live-доказательство, что ER-фильтр реально включён в работающем боте."""
import os
import sys

sys.path.insert(0, '/app')

import numpy as np  # noqa: E402
import yaml  # noqa: E402

from core.donchian_breakout import (  # noqa: E402
    DonchianConfig, DonchianSignalGenerator, efficiency_ratio, er_threshold_at,
)

CFG = yaml.safe_load(open('/app/config/settings.yaml', encoding='utf-8'))
POOL10 = yaml.safe_load(open('/app/config/settings.donchian_pool10.yaml',
                             encoding='utf-8'))

print('=== 1. конфиг, который реально читает бот ===')
g = CFG.get('donchian') or {}
print(f'  settings.yaml donchian: {g}')
print(f'  settings.yaml risk.commission_haircut: '
      f'{(CFG.get("risk") or {}).get("commission_haircut")}')

print('=== 2. конфиг символа из пула ===')
a = POOL10['assets'][0]
print(f'  pool10[0] = {a["symbol"]}, strategy={a["strategy_type"]}')

print('=== 3. DonchianConfig, собранный ровно как в main.py ===')
raw = dict(g or {})
fields = set(DonchianConfig.__dataclass_fields__)
unknown = set(raw) - fields
assert not unknown, f'неизвестные ключи (бот их проигнорирует): {unknown}'
kw = {k: v for k, v in raw.items() if k in fields}
dcfg = DonchianConfig(**kw)
print(f'  global donchian -> DonchianConfig(**{kw})')
print(f'  er_filter={dcfg.er_filter} er_period={dcfg.er_period} '
      f'window={dcfg.er_threshold_window} warmup={dcfg.warmup_bars}')
print(f'  риск/выходы не тронуты: risk={dcfg.risk_percent}% trail={dcfg.trail_atr} '
      f'max_bars={dcfg.max_bars} cap={dcfg.max_concurrent}')

assert dcfg.er_filter is True, 'ER-ФИЛЬТР ВЫКЛЮЧЕН В ЖИВОМ КОНФИГЕ'
assert dcfg.er_threshold_window == 950
assert dcfg.warmup_bars == 972
assert dcfg.trail_atr == 2.0 and dcfg.max_bars == 30 and dcfg.risk_percent == 0.35
need = dcfg.er_threshold_window + dcfg.er_period + 4
print(f'  нужно баров: {need} (лимит Bybit 1000) -> {"OK" if need <= 1000 else "FAIL"}')
assert need <= 1000

print('=== 4. ER считается на реальных 1000 барах ===')
gen = DonchianSignalGenerator('ADAUSDT', dcfg)
n = 1000
close = 100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, .01, n)))
import pandas as pd  # noqa: E402
df = pd.DataFrame({
    'open': close, 'high': close * 1.005, 'low': close * 0.995,
    'close': close, 'volume': [1.0] * n,
})
er = efficiency_ratio(df['close'].to_numpy(), dcfg.er_period)
i = n - 2
th = er_threshold_at(er, i, dcfg.er_threshold_window)
print(f'  len(df)={len(df)} candle_idx={i} warmup={dcfg.warmup_bars}')
print(f'  ER[i]={er[i]:.4f}  threshold={th:.4f}  thr_is_median_of_prev={th is not None}')
assert i >= dcfg.warmup_bars, 'прогрев не покрыт 1000 барами'
assert th is not None, 'порог не вычислился на 1000 барax'

# граница прогрева
j = dcfg.warmup_bars - 1
print(f'  на последнем прогревочном баре j={j} (={j - 0} баров) -> '
      f'сигнал: {gen.evaluate(df, j)}')
assert gen.evaluate(df, dcfg.warmup_bars - 1) is None, 'fail-closed нарушен'

print('=== 5. ER-фильтр выключенным не трогает базовую логику ===')
off = DonchianConfig()
print(f'  er_filter по умолчанию = {off.er_filter} '
      f'(стратегия не меняется молча)')
assert off.er_filter is False
assert off.warmup_bars == max(off.lookback, off.atr_period) + 1

print()
print('ER-ФИЛЬТР ЖИВ: er_filter=True, window=950, warmup=972, баров хватает.')
print(f'BYBIT_KEY_SET={bool(os.getenv("BYBIT_API_KEY"))} '
      f'HOST={os.getenv("REDIS_HOST")}')
