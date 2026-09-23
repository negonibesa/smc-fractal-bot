import os, signal, subprocess

# Kill old rolling_optimize process
result = subprocess.run(['bash', '-c', 'pkill -f rolling_optimize 2>/dev/null; echo killed'], capture_output=True, text=True)
print(result.stdout.strip())

# Clear old log
with open('/app/backtest_results/wf_run.log', 'w') as f:
    f.write('')

# Start new process
proc = subprocess.Popen(
    ['python', '-u', '/app/rolling_optimize.py'],
    stdout=open('/app/backtest_results/wf_run.log', 'w'),
    stderr=subprocess.STDOUT,
    start_new_session=True
)
print(f'PID: {proc.pid}')
