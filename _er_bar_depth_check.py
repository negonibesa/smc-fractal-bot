"""Сколько 4H баров реально отдаёт Bybit для пула10 при limit=1000.

Если у монеты меньше 974 баров, ER-фильтр fail-closed и её торговля
молча выключена. Это нужно видеть сразу, а не через месяц по нулю сделок.
"""
import os
import sys

sys.path.insert(0, '/app')

import pandas as pd  # noqa: E402
import yaml  # noqa: E402
from core.bybit_client import BybitClient  # noqa: E402

CFG = yaml.safe_load(open('/app/config/settings.donchian_pool10.yaml',
                           encoding='utf-8'))
pool = [a['symbol'] for a in CFG['assets']]

c = BybitClient(api_key=os.environ['BYBIT_API_KEY'],
                api_secret=os.environ['BYBIT_API_SECRET'])
NEED = 974   # er_period 20 + window 950 + 2 (незакрытый последний) + запас


def fetch(symbol, limit):
    res = c.get_klines(symbol, interval='240', limit=limit)
    rows = res.get('list', [])
    df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low',
                                     'close', 'volume', 'turnover'])
    return df.sort_values('timestamp').reset_index(drop=True)


print(f'{"символ":<10} {"bars@1000":>10} {"bars@200":>9}  {"старт 4H":>12}  статус')
print('-' * 62)
bad = []
for s in pool:
    try:
        d1 = fetch(s, 1000)
        d2 = fetch(s, 200)
        n1, n2 = len(d1), len(d2)
        ok = n1 >= NEED
        if not ok:
            bad.append((s, n1))
        first = str(pd.to_datetime(pd.to_numeric(d1['timestamp']),
                                    unit='ms').iloc[0])[:10] if n1 else '-'
        print(f'{s:<10} {n1:>10} {n2:>9}  {first:>12}  '
              f'{"OK" if ok else "!! < " + str(NEED)}')
    except Exception as e:  # noqa: BLE001
        bad.append((s, str(e)))
        print(f'{s:<10} {"ERR":>10} {"-":>9}  {"-":>12}  {e}')

print('-' * 62)
if bad:
    print('НЕ ХВАТАЕТ ИСТОРИИ (торговля выключена fail-closed):')
    for s, n in bad:
        print(f'   {s}: {n}')
    sys.exit(1)
print(f'Все {len(pool)} монет имеют >= {NEED} баров — ER-фильтр рабочий везде.')
