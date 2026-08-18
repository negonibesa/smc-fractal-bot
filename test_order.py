"""
Test: минимальный ордер с SL/TP.
"""
import os, math, time
from dotenv import load_dotenv
from core.bybit_client import BybitClient

load_dotenv()

client = BybitClient(
    api_key=os.getenv('BYBIT_API_KEY'),
    api_secret=os.getenv('BYBIT_API_SECRET'),
    testnet=True
)

SYMBOL = "BNBUSDT"

print("=" * 50)
print("  STEP 1: Закрыть текущую позицию")
print("=" * 50)

pos = client.get_position(SYMBOL)
if pos and float(pos.get('size', 0)) > 0:
    side = pos.get('side')
    size = pos.get('size')
    close_side = "Sell" if side == "Buy" else "Buy"
    print(f"  {side} {size} -> закрываю")
    client.cancel_all_orders(SYMBOL)
    client.place_market_order(SYMBOL, close_side, str(size), reduce_only=True)
    print("  Закрыто!")
    time.sleep(1)
else:
    print("  Нет позиций")

print("\n" + "=" * 50)
print("  STEP 2: Открыть BUY 0.01 BNB")
print("=" * 50)

# Свежая цена
ticker = client.get_ticker(SYMBOL)
price = float(ticker['lastPrice'])
print(f"  Цена: {price}")

qty = 0.01
notional = qty * price
print(f"  Qty: {qty} BNB ~ ${notional:.2f}")

# Параметры инструмента
inst = client.get_instruments(SYMBOL)
tick = float(inst.get('priceFilter', {}).get('tickSize', 0.01))

# Открыть
result = client.place_market_order(SYMBOL, "Buy", str(qty))
print(f"  orderId: {result.get('orderId', '')}")

time.sleep(2)

# Получить позицию и текущую цену
pos = client.get_position(SYMBOL)
if not pos:
    print("  Позиция не найдена!")
    exit()

entry = float(pos.get('avgPrice', price))
print(f"  Entry: {entry}")

# SL/TP на основе ENTRY
sl_pct = 0.015   # 1.5% стоп (чуть шире чтобы не сработать сразу)
tp_pct = 0.02    # 2% тейк

sl = round(entry * (1 - sl_pct) / tick) * tick
tp = round(entry * (1 + tp_pct) / tick) * tick

print(f"  SL: {sl} (-{sl_pct*100}%)")
print(f"  TP: {tp} (+{tp_pct*100}%)")

# Установить SL/TP
print("\n[3] Устанавливаю SL/TP ...")
try:
    client.set_trading_stop(SYMBOL, stop_loss=str(sl), take_profit=str(tp),
                            sl_trigger_by="LastPrice", tp_trigger_by="LastPrice")
    print("  OK!")
except Exception as e:
    print(f"  set_trading_stop: {e}")
    
    # Попробуем с бОльшим буфером
    sl2 = round(entry * (1 - 0.02) / tick) * tick
    tp2 = round(entry * (1 + 0.025) / tick) * tick
    print(f"  Пробую SL={sl2}, TP={tp2} ...")
    try:
        client.set_trading_stop(SYMBOL, stop_loss=str(sl2), take_profit=str(tp2),
                                sl_trigger_by="LastPrice", tp_trigger_by="LastPrice")
        print("  OK с расширенным диапазоном!")
    except Exception as e2:
        print(f"  Все равно ошибка: {e2}")

# Финал
print("\n" + "=" * 50)
print("  РЕЗУЛЬТАТ")
print("=" * 50)

pos = client.get_position(SYMBOL)
if pos:
    print(f"  Side:   {pos.get('side')}")
    print(f"  Size:   {pos.get('size')} BNB")
    print(f"  Entry:  {pos.get('avgPrice')}")
    print(f"  PnL:    {pos.get('unrealisedPnl')} USDT")
    print(f"  SL:     {pos.get('stopLoss') or 'N/A'}")
    print(f"  TP:     {pos.get('takeProfit') or 'N/A'}")

wallet = client.get_wallet_balance()
print(f"  Equity: {wallet.get('totalEquity')} USDT")

# Открытые ордера
orders = client.get_open_orders(SYMBOL)
print(f"  Open orders: {len(orders)}")
for o in orders:
    print(f"    {o.get('side')} {o.get('qty')} @ {o.get('price', 'N/A')} type={o.get('orderType')}")

print("\n" + "=" * 50)
print("  DONE")
print("=" * 50)
