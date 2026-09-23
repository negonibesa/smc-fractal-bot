import re
with open('/app/backtest_results/wf_run.log', 'rb') as f:
    data = f.read()
text = data.decode('utf-8', errors='replace')
lines = text.split('\n')
# Filter for important lines
keywords = ['TRUE', 'SUMMARY', 'AVG', 'MIN', 'Result:', 'Split', 'OPTIMIZE', 'DONE']
for line in lines[-30:]:
    stripped = line.strip()
    if stripped and any(k in stripped for k in keywords):
        print(stripped)
