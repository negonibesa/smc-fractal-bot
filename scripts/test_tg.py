import os, requests
from dotenv import load_dotenv
load_dotenv()
token = os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")
print("Token:", token[:10] + "...")
print("Chat ID:", chat_id)

# Test send
r = requests.post(
    f"https://api.telegram.org/bot{token}/sendMessage",
    json={"chat_id": chat_id, "text": "TEST: Listener ping"},
    timeout=10,
)
print("Send:", r.status_code, r.text[:200])

# Flush old updates
r2 = requests.get(f"https://api.telegram.org/bot{token}/getUpdates?offset=-1", timeout=10)
data = r2.json()
print("Flushed:", len(data.get("result", [])), "updates")
