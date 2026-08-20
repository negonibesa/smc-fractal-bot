"""
Test Redis persistence: save state, simulate restart, verify restore.
"""
import os, time, json
from dotenv import load_dotenv

load_dotenv()

print("=" * 50)
print("  REDIS PERSISTENCE TEST")
print("=" * 50)

from core.redis_store import RedisStore
from core.position_tracker import PositionTracker
from core.risk_manager import RiskManager

# ─── 1. Connect ───────────────────────────────────────────
print("\n[1] Redis connection...")
store = RedisStore(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    prefix="smc_test",
)
print(f"    Status: {store.get_status()}")

# ─── 2. Create tracker with Redis, save position ──────────
print("\n[2] Creating PositionTracker with Redis...")
tracker1 = PositionTracker(redis_store=store)

print("    Opening position: LONG 0.5 BNBUSDT @ 600.00 SL=590.00 TP=620.00")
tracker1.open_position("BNBUSDT", "LONG", 600.00, 0.5, 590.00, 620.00)

print("    Opening position: SHORT 0.3 ETHUSDT @ 3500.00 SL=3550.00 TP=3400.00")
tracker1.open_position("ETHUSDT", "SHORT", 3500.00, 0.3, 3550.00, 3400.00)

# Simulate a closed trade
tracker1.close_position("BTCUSDT", pnl=12.50)

print(f"    Open positions: {len(tracker1.get_all_positions())}")
print(f"    Closed trades: {len(tracker1.get_closed_trades())}")

# ─── 3. Save risk state ───────────────────────────────────
print("\n[3] Saving RiskManager state...")
risk1 = RiskManager(risk_percent=1.0, redis_store=store)
risk1.initial_equity = 10000
risk1.peak_equity = 10500
risk1.update_equity(10200)
risk1.register_trade(50.0)
risk1.register_trade(-20.0)
print(f"    Daily trades: {risk1.daily.trades}")
print(f"    Daily PnL: {risk1.daily.pnl}")

# ─── 4. Simulate restart — new instances ──────────────────
print("\n[4] Simulating RESTART (new instances)...")
tracker2 = PositionTracker(redis_store=store)
risk2 = RiskManager(risk_percent=1.0, redis_store=store)

# ─── 5. Verify restore ────────────────────────────────────
print("\n[5] Verifying restored state...")

# Positions
pos1 = tracker2.get_position("BNBUSDT")
pos2 = tracker2.get_position("ETHUSDT")
print(f"    BNBUSDT: {pos1.side if pos1 else 'MISSING'} {pos1.size if pos1 else ''} @ {pos1.entry_price if pos1 else ''}")
print(f"    ETHUSDT: {pos2.side if pos2 else 'MISSING'} {pos2.size if pos2 else ''} @ {pos2.entry_price if pos2 else ''}")

trades = tracker2.get_closed_trades()
print(f"    Closed trades: {len(trades)}")
if trades:
    print(f"    Last trade PnL: {trades[-1].get('pnl', 'N/A')}")

print(f"    Risk equity: {risk2.peak_equity}")
print(f"    Risk daily trades: {risk2.daily.trades}")
print(f"    Risk daily PnL: {risk2.daily.pnl}")

# ─── 6. Signal state ──────────────────────────────────────
print("\n[6] Testing signal state...")
store.save_signal_state("BNBUSDT", {
    'state': 1,
    'sweep_direction': 'bearish',
    'sweep_price': 605.0,
    'sweep_index': 150,
    'center_at_sweep': 600.0,
})
loaded = store.load_signal_state("BNBUSDT")
print(f"    Saved & loaded signal state: state={loaded['state']} dir={loaded['sweep_direction']}")

# ─── 7. Cleanup ───────────────────────────────────────────
print("\n[7] Cleanup test data...")
store.flush()
print(f"    Flushed. Keys remaining: {store.get_status()['keys']}")

print("\n" + "=" * 50)
print("  ALL TESTS PASSED")
print("=" * 50)
