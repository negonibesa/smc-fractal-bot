import json, time, requests
from collections import defaultdict
from urllib.parse import urlencode
import hmac, hashlib
import redis

API_KEY = 'XSuuulrhlNGtVd4zYI'
API_SECRET = 'nLa1diavcn7IqJWTugJL23eKeXqYXVM6f4fN'
BASE = 'https://api-demo.bybit.com'

def sign(params, ts):
    param_str = urlencode(sorted(params.items()))
    sign_str = f'{ts}{API_KEY}50000{param_str}'
    return hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()

s = requests.Session()
s.headers.update({'Content-Type': 'application/json', 'X-BAPI-API-KEY': API_KEY})

ts = int(time.time() * 1000)
params = {'category': 'linear', 'limit': 50, 'timestamp': ts}
sig = sign(params, ts)
s.headers.update({
    'X-BAPI-TIMESTAMP': str(ts),
    'X-BAPI-SIGN': sig,
    'X-BAPI-RECV-WINDOW': '50000',
})
resp = s.get(f'{BASE}/v5/position/closed-pnl', params=params, timeout=30)
data = resp.json()

if data.get('retCode') != 0:
    print(f'Error: {data}')
    exit(1)

trades = data['result']['list']
print(f'Bybit closed trades: {len(trades)}')

total_pnl = 0.0
wins = 0
losses = 0
by_sym = defaultdict(lambda: {'trades': 0, 'wins': 0, 'pnl': 0.0})

for t in trades:
    pnl = float(t.get('closedPnl', 0))
    sym = t.get('symbol', '?')
    total_pnl += pnl
    if pnl > 0:
        wins += 1
    else:
        losses += 1
    by_sym[sym]['trades'] += 1
    by_sym[sym]['pnl'] += pnl
    if pnl > 0:
        by_sym[sym]['wins'] += 1

total = wins + losses
wr = wins / total * 100 if total > 0 else 0

print(f'\n=== TOTAL ===')
print(f'Trades: {total}')
print(f'Wins: {wins}, Losses: {losses}')
print(f'Win Rate: {wr:.1f}%')
print(f'Total PnL: ${total_pnl:.2f}')

print(f'\n=== PER SYMBOL ===')
for sym in sorted(by_sym.keys()):
    s = by_sym[sym]
    swr = s['wins'] / s['trades'] * 100 if s['trades'] > 0 else 0
    print(f'  {sym}: {s["trades"]} trades, {swr:.0f}% WR, PnL=${s["pnl"]:.2f}')

# Sync to Redis
try:
    r = redis.Redis(host='redis', port=6379, decode_responses=True)

    # Rebuild risk state
    wins_list = [float(t.get('closedPnl', 0)) > 0 for t in trades]
    risk = {
        'initial_equity': 166000,
        'peak_equity': 170011,
        'trading_paused': False,
        'pause_reason': '',
        'daily': {
            'date': time.strftime('%Y-%m-%d'),
            'trades': total,
            'wins': wins,
            'losses': losses,
            'pnl': total_pnl,
            'consecutive_losses': 0,
        }
    }

    # Calculate consecutive losses from last trades
    cl = 0
    for t in reversed(trades):
        if float(t.get('closedPnl', 0)) < 0:
            cl += 1
        else:
            break
    risk['daily']['consecutive_losses'] = cl

    r.set('smc:risk', json.dumps(risk))

    # Clear old trades and rebuild from Bybit
    r.delete('smc:trades:closed')
    for t in trades:
        pnl = float(t.get('closedPnl', 0))
        side = 'LONG' if t.get('side') == 'Buy' else 'SHORT'
        trade = {
            'symbol': t.get('symbol', '?'),
            'side': side,
            'entry': float(t.get('avgPrice', 0)),
            'entry_time': 0,
            'exit_price': float(t.get('avgPrice', 0)) + (pnl / float(t.get('size', 1)) if float(t.get('size', 1)) > 0 else 0),
            'exit_reason': t.get('stopOrderType', ''),
            'size': float(t.get('size', 0)),
            'pnl': pnl,
            'close_time': 0,
            'max_pnl_risk': 0,
        }
        r.rpush('smc:trades:closed', json.dumps(trade))

    print(f'\nRedis synced: {r.llen("smc:trades:closed")} trades, risk updated')
except Exception as e:
    print(f'Redis sync failed: {e}')
