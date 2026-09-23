with open('/app/backtest_results/wf_run.log', 'rb') as f:
    data = f.read()
text = data.decode('utf-8', errors='replace')
lines = text.split('\n')
for line in lines[-10:]:
    print(repr(line.strip()))
