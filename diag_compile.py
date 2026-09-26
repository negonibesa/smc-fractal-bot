"""Компиляция всех изменённых модулей внутри контейнера. Ничего не запускает."""
from __future__ import annotations

import py_compile
import sys

FILES = [
    'main.py',
    'core/donchian_breakout.py',
    'core/order_executor.py',
    'core/position_tracker.py',
    'core/risk_manager.py',
    'core/dashboard.py',
]

failed = False
for f in FILES:
    try:
        py_compile.compile(f, doraise=True)
        print('  ok  ', f)
    except Exception as e:
        print('  FAIL', f, e)
        failed = True

if not failed:
    # импорт main тянет flask и requests — проверяем, что они на месте
    try:
        import yaml  # noqa: F401
        from core import donchian_breakout  # noqa: F401
        print('  ok   импорт core.donchian_breakout')
    except Exception as e:
        print('  FAIL импорт:', e)
        failed = True

sys.exit(1 if failed else 0)
