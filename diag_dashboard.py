"""Живая проверка дашборда: тянет /api/status с localhost, авторизуясь паролем
из окружения контейнера. Сам пароль не печатается. Печатается только JSON-ответ.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request


def get(path: str):
    user = os.getenv('DASH_USER', 'admin')
    pw = os.getenv('DASH_PASS', '')
    if not pw:
        print('DASH_PASS не задан в окружении контейнера')
        sys.exit(1)
    token = base64.b64encode(f'{user}:{pw}'.encode()).decode()
    req = urllib.request.Request(
        f'http://127.0.0.1{path}',
        headers={'Authorization': f'Basic {token}'},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read().decode('utf-8', 'replace')


def main() -> int:
    st, body = get('/api/status')
    print(f'HTTP {st}, {len(body)} байт')
    d = json.loads(body)

    print()
    print('=== bot ===')
    for k, v in d['bot'].items():
        if k == 'coins':
            continue
        print(f'  {k}: {v}')

    print()
    print('=== account ===')
    for k, v in d['account'].items():
        print(f'  {k}: {v}')

    print()
    print('=== risk ===')
    for k, v in d['risk'].items():
        print(f'  {k}: {v}')

    print()
    print(f'=== positions: {len(d["positions"])} ===')
    for p in d['positions']:
        print('  ', p)

    print()
    print(f'=== signals: {len(d["signals"])} ===')
    for s in d['signals'][:5]:
        print('  ', s)

    print()
    print(f'=== regime: {len(d["regime"])} ===')
    for k, v in list(d['regime'].items())[:5]:
        print('  ', k, v)

    print()
    print(f'=== logs: {len(d["logs"])} строк ===')
    for line in d['logs'][-5:]:
        print('  ', line[:150])

    print()
    print('=== coins (per-coin trades/pnl) ===')
    for c in d['bot']['coins']:
        print(f"   {c['symbol']:<10} trades={c['trades']:<4} pnl={c['pnl']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
