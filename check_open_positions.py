"""Read-only аудит состояния Bybit Demo перед переключением стратегии.

Ничего не меняет: только GET-методы. Нужен, чтобы убедиться, что переключение
на Donchian не осиротит позиции по монетам, выпадающим из нового пула
(LTC, XMR, SOL), и увидеть реальный equity вместо значения в CLAUDE.md.

Запуск (на VPS, внутри контейнера):
    docker exec smc-bot python check_open_positions.py
"""
from __future__ import annotations

import os
import sys

from core.bybit_client import BybitClient

# монеты нового пула Donchian (H26)
POOL10 = {
    'ADAUSDT', 'ARBUSDT', 'DOGEUSDT', 'ENAUSDT', 'HBARUSDT',
    'LINKUSDT', 'NEARUSDT', 'SUIUSDT', 'XLMUSDT', 'XRPUSDT',
}
# монеты старого пула ZDev, которых в новом пуле нет
DROPPED = {'LTCUSDT', 'XMRUSDT', 'SOLUSDT', 'APTUSDT', 'TONUSDT', 'ETHUSDT'}


def main():
    key = os.getenv('BYBIT_API_KEY', '')
    secret = os.getenv('BYBIT_API_SECRET', '')
    if not key or not secret:
        print('BYBIT_API_KEY / BYBIT_API_SECRET не заданы в окружении')
        return 1
    demo = os.getenv('BYBIT_DEMO', 'true').lower() == 'true'
    client = BybitClient(key, secret, demo=demo)

    print('=' * 74)
    print(f'Bybit {"DEMO" if demo else "MAINNET"} — read-only аудит перед переключением')
    print('=' * 74)

    try:
        # get_wallet_balance() уже разворачивает result и берёт list[0],
        # поэтому лезть в ['result']['list'] здесь нельзя — вернёт пусто.
        info = client.get_wallet_balance()
    except Exception as e:
        print(f'баланс недоступен: {e}')
        info = {}

    eq = float(info.get('totalEquity', 0) or 0)
    avail = float(info.get('totalAvailableBalance', 0) or 0)
    used = float(info.get('totalInitialMargin', 0) or 0)
    print(f'equity {eq:,.2f} USDT   доступно {avail:,.2f}   маржа {used:,.2f}')
    if eq <= 0:
        print('ВНИМАНИЕ: equity == 0. Donchian считает размер от equity,')
        print('при нуле все сделки получат нулевой размер и бот не сможет торговать.')
    print('')

    try:
        positions = client.get_positions()
    except Exception as e:
        print(f'позиции недоступны: {e}')
        return 1

    live = []
    for p in positions:
        try:
            sym = p.get('symbol', '')
            size = float(p.get('size', 0) or 0)
            if size == 0:
                continue
            live.append((sym, size, float(p.get('side', '0') or 0),
                         float(p.get('avgPrice', 0) or 0),
                         float(p.get('markPrice', 0) or 0),
                         float(p.get('unrealisedPnl', 0) or 0)))
        except (TypeError, ValueError):
            continue

    if not live:
        print('ОТКРЫТЫХ ПОЗИЦИЙ НЕТ')
        print('Переключение на Donchian безопасно: осиротить нечего.')
    else:
        print(f'ОТКРЫТЫХ ПОЗИЦИЙ: {len(live)}')
        print('-' * 74)
        print(f'{"символ":<12}{"сторона":>8}{"размер":>14}{"вход":>14}'
              f'{"uPnL":>10}  пул')
        print('-' * 74)
        orphans = []
        for sym, size, side, avg, mark, pnl in live:
            st = 'LONG' if side > 0 else 'SHORT'
            if sym in POOL10:
                tag = 'pool10'
            elif sym in DROPPED:
                tag = 'ВЫПАДАЕТ ИЗ ПУЛА'
                orphans.append(sym)
            else:
                tag = 'НЕ В НОВОМ ПУЛЕ'
                orphans.append(sym)
            print(f'{sym:<12}{st:>8}{size:>14.6f}{avg:>14.6f}{pnl:>10.2f}  {tag}')
        print('-' * 74)
        if orphans:
            print(f'ВНИМАНИЕ: {len(orphans)} позиций вне нового пула: '
                  f'{" ".join(sorted(orphans))}')
            print('Бот перестанет ими управлять после переключения '
                  '(нет трейлинга, time-stop и учёта PnL).')
            print('Закрой их на бирже ПЕРЕД деплоем.')
        else:
            print('Все позиции входят в новый пул — переключение безопасно.')

    try:
        orders = client.get_open_orders('BTCUSDT')
        orders = [o for o in orders if o.get('symbol')]
    except TypeError:
        # get_open_orders() требует symbol: без него Bybit отвечает 10001
        orders = []
    except Exception as e:
        print(f'\nоткрытые ордера недоступны: {e}')
        return 0
    if orders:
        print(f'\nВИСЯЩИЕ ОРДЕРА: {len(orders)} — бот их не отменит, '
              f'разрешить/отменить вручную:')
        for o in orders:
            print(f"  {o.get('symbol')} {o.get('side')} "
                  f"{o.get('qty')} @ {o.get('price')} "
                  f"reduceOnly={o.get('reduceOnly', '0')}")
    else:
        print('\nвисящих ордеров нет')

    return 0


if __name__ == '__main__':
    sys.exit(main())
