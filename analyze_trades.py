"""Analyze ETH trade breakdown to understand signal issues."""
import sys, time, yaml, ccxt, pandas as pd, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from backtest import run_backtest
from main import SignalGenerator

def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None):
    all_candles = []
    if since is None:
        since = exchange.parse8601('2022-01-01T00:00:00Z')
    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        except:
            time.sleep(5)
            try:
                candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            except:
                break
        if not candles:
            break
        all_candles.extend(candles)
        since = candles[-1][0] + 1
        if len(candles) < 1000:
            break
        time.sleep(0.2)
    if not all_candles:
        return pd.DataFrame()
    df = pd.DataFrame(all_candles, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df

def fetch_funding_rates(exchange, symbol):
    all_rates = []
    end_time = None
    while True:
        params = {}
        if end_time is not None:
            params['endTime'] = end_time
        try:
            rates = exchange.fetch_funding_rate_history(symbol, limit=200, params=params)
        except:
            time.sleep(3)
            try:
                rates = exchange.fetch_funding_rate_history(symbol, limit=200, params=params)
            except:
                break
        if not rates:
            break
        all_rates.extend(rates)
        end_time = rates[0]['timestamp'] - 1
        if len(rates) < 200:
            break
        time.sleep(0.15)
    if not all_rates:
        return pd.DataFrame(columns=['timestamp','rate'])
    df = pd.DataFrame([{
        'timestamp': pd.Timestamp(r['timestamp'], unit='ms'),
        'rate': r['fundingRate']
    } for r in all_rates])
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df

def main():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})

    bybit = ccxt.bybit({'enableRateLimit': True})

    # ETH with optimized params
    eth_cfg = {}
    for asset in config.get('assets', []):
        if asset['symbol'] == 'ETHUSDT' and asset.get('enabled'):
            eth_cfg = asset.get('config', {})
            break

    sig_config = {'strategy': eth_cfg.get('strategy', {}), 'filters': eth_cfg.get('filters', {})}
    gen = SignalGenerator(sig_config, symbol='ETHUSDT')

    df = fetch_ohlcv(bybit, 'ETH/USDT:USDT', timeframe='4h')
    print(f"ETH 4H candles: {len(df)}")
    
    funding = fetch_funding_rates(bybit, 'ETH/USDT:USDT')
    
    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            sig['timestamp'] = str(df['timestamp'].iloc[i])
            signals.append(sig)

    bt_df = df.copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]

    tf = None
    if funding is not None and not funding.empty:
        tf = funding[
            (funding['timestamp'] >= df['timestamp'].iloc[0]) &
            (funding['timestamp'] <= df['timestamp'].iloc[-1])
        ]
        if tf.empty:
            tf = None

    trades, metrics = run_backtest(
        bt_df, bs,
        initial_balance=10000,
        risk_percent=risk_config.get('risk_percent', 1.0),
        commission=risk_config.get('commission', 0.001),
        slippage=risk_config.get('slippage', 0.0005),
        stop_buffer=risk_config.get('stop_buffer', 0.002),
        breakeven_at=eth_cfg.get('trailing', {}).get('breakeven_at', 0.3),
        trailing_activate=eth_cfg.get('trailing', {}).get('trail_activate', 0.8),
        trailing_step=eth_cfg.get('trailing', {}).get('trail_step', 0.3),
        max_leverage=risk_config.get('max_leverage', 10),
        dynamic_risk=dynamic_risk,
        cooldown_hours=4.0,
        max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
        max_daily_trades=risk_config.get('max_daily_trades', 20),
        max_consecutive_losses=risk_config.get('max_consecutive_losses', 3),
        funding_rates=tf,
    )

    # Analyze trade reasons
    reasons = {}
    for t in trades:
        r = t['exit_reason']
        if r not in reasons:
            reasons[r] = {'count': 0, 'total_pnl': 0, 'wins': 0, 'losses': 0}
        reasons[r]['count'] += 1
        reasons[r]['total_pnl'] += t['pnl']
        if t['pnl'] > 0:
            reasons[r]['wins'] += 1
        else:
            reasons[r]['losses'] += 1

    print(f"\n{'='*60}")
    print(f"  ETH 4H — TRADE ANALYSIS")
    print(f"{'='*60}")
    print(f"  Total trades: {len(trades)}")
    print(f"  Total PnL: ${sum(t['pnl'] for t in trades):+.2f}")
    print(f"\n  Exit Reasons:")
    print(f"  {'Reason':<20} {'Count':>6} {'Wins':>6} {'Losses':>6} {'WinRate':>8} {'PnL':>10}")
    print(f"  {'-'*60}")
    for r, data in sorted(reasons.items(), key=lambda x: -x[1]['count']):
        wr = data['wins'] / data['count'] if data['count'] > 0 else 0
        print(f"  {r:<20} {data['count']:>6} {data['wins']:>6} {data['losses']:>6} {wr:>7.0%} {data['total_pnl']:>+10.2f}")

    # PnL distribution
    pnls = [t['pnl'] for t in trades]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    
    print(f"\n  PnL Distribution:")
    print(f"  Winners: {len(winners)} trades, avg ${np.mean(winners):+.2f}, total ${sum(winners):+.2f}")
    print(f"  Losers:  {len(losers)} trades, avg ${np.mean(losers):+.2f}, total ${sum(losers):+.2f}")
    print(f"  Avg Win/Loss ratio: {abs(np.mean(winners)/np.mean(losers)):.2f}x" if losers else "")

    # Breakeven analysis
    be_trades = [t for t in trades if t['exit_reason'] == 'BREAKEVEN']
    print(f"\n  Breakeven trades: {len(be_trades)}")
    if be_trades:
        be_pnls = [t['pnl'] for t in be_trades]
        print(f"  BE PnL: avg ${np.mean(be_pnls):+.2f}, total ${sum(be_pnls):+.2f}")

if __name__ == '__main__':
    main()
