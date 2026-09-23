import re
with open('/app/backtest_results/wf_run.log', 'rb') as f:
    data = f.read()
text = data.decode('utf-8', errors='replace')
for line in text.split('\n'):
    s = line.strip()
    if not s:
        continue
    if 'TIMEOUT' in s or 'Fetching funding' in s or 'Funding page' in s:
        continue
    if any(k in s for k in ['TRUE', 'SUMMARY', 'AVG', 'MIN PF', 'Result:', 'Split ', 'Done in', 'candles', 'OPTIMIZE', 'Params changed', 'Testing']):
        print(s)
