"""Удалить только те smc:signal:* ключи, символ которых не входит в текущий пул.

Полный clear_signal_states() НЕ используется намеренно: если позже появится
SMC-монета, её состояние должно сохраняться. Удаляются лишь символы, выпавшие
из конфига, — их состояние больше никто не прочитает и не перезапишет
(_save_signal_state вызывается только для strategy_type == 'smc',
main.py:1165, а load_signal_state — только для символов из конфига).

Скрипт выполняется ВНУТРИ контейнера smc-bot и ходит в Redis напрямую:
внутри контейнера бинаря docker нет.
"""
from __future__ import annotations

import json
import os
import sys

import redis
import yaml


def main() -> int:
    with open('config/settings.yaml', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    pool = {a['symbol'] for a in cfg['assets'] if a.get('enabled')}

    r = redis.Redis(host=os.getenv('REDIS_HOST', 'redis'),
                    port=int(os.getenv('REDIS_PORT', 6379)),
                    db=0, decode_responses=True)
    r.ping()

    keys = sorted(r.scan_iter('smc:signal:*'))
    print(f'текущий пул ({len(pool)}): {" ".join(sorted(pool))}')
    print(f'ключей smc:signal:*, найдено {len(keys)}')
    print()

    stale, keep = [], []
    for k in keys:
        sym = k.split('signal:', 1)[1] if 'signal:' in k else ''
        (keep if sym in pool else stale).append((k, sym))

    if keep:
        print('ОСТАВЛЯЮ (символ в пуле):')
        for k, _s in keep:
            print(f'  {k}')
        print()

    if not stale:
        print('Удалять нечего — все ключи относятся к символам из пула.')
        return 0

    print('УДАЛЯЮ (символ вне пула):')
    for k, s in stale:
        raw = r.get(k) or ''
        try:
            d = json.loads(raw)
            preview = ', '.join(f'{kk}={vv}' for kk, vv in list(d.items())[:4]
                                if not isinstance(vv, (dict, list)))
            preview = f'поля: {preview}' if preview else f'поля: {list(d.keys())[:6]}'
        except Exception:
            preview = f'{len(raw)} байт, не JSON'
        print(f'  {s:<10} {preview}')
        r.delete(k)
        print('    удалён подтверждён' if not r.exists(k)
              else '    ОШИБКА: ключ остался')

    print()
    left = sorted(r.scan_iter('smc:signal:*'))
    print(f'осталось smc:signal:* — {len(left)}: {left}')
    print()
    print('прочие ключи бота НЕ трогаю (trades:closed хранит историю сделок):')
    for k in sorted(r.scan_iter('smc:*')):
        if not k.startswith('smc:signal:'):
            print(f'  {k}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
