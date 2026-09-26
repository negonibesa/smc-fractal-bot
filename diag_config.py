"""Показать, что бот реально видит в конфиге: стратегии, риск, пул. Только чтение."""
from __future__ import annotations

import sys

import yaml

path = sys.argv[1] if len(sys.argv) > 1 else 'config/settings.yaml'
with open(path, encoding='utf-8') as f:
    c = yaml.safe_load(f)

assets = c['assets']
risk = c['risk']
print(f'файл: {path}')
print('монет:', len(assets))
print('стратегии:', sorted({a.get('strategy_type') for a in assets}))
print('risk_percent:', risk.get('risk_percent'),
      '| cap:', risk.get('max_concurrent_positions'),
      '| halt:', risk.get('max_drawdown'),
      '| haircut:', risk.get('commission_haircut'))
print('список:', ' '.join(a['symbol'] for a in assets))
try:
    from core.donchian_breakout import DonchianConfig
    d = DonchianConfig()
    print('DonchianConfig по умолчанию: lb=%s atr=%s trail=%s max_bars=%s risk=%s'
          % (d.lookback, d.atr_period, d.trail_atr, d.max_bars, d.risk_percent))
except Exception as e:
    print('donchian_breakout недоступен:', e)
