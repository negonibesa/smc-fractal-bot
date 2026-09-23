import os
with open('/app/backtest_results/wf_run.log', 'rb') as f:
    data = f.read()
lines = data.decode('utf-8', errors='replace').split('\n')
for line in lines[:20]:
    if 'TIMEOUT' not in line and line.strip():
        print(line)
