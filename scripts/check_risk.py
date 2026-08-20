import sys; sys.path.insert(0, '/app')
from dotenv import load_dotenv; load_dotenv('/app/.env')
import os, json
from core.redis_store import RedisStore
r = RedisStore(os.getenv('REDIS_HOST','redis'), int(os.getenv('REDIS_PORT',6379)))
state = r.load_risk_state()
print(json.dumps(state, indent=2, default=str))
print("\n--- Risk get_status ---")
from core.risk_manager import RiskManager
rm = RiskManager(risk_percent=1.5, redis=r)
print(json.dumps(rm.get_status(), indent=2, default=str))
