"""A/B test breakeven_at values for ETH."""
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

def run_test(df, signals_dict, funding, breakeven_at_val, risk_config, dynamic_risk, trailing_cfg):
    bt_df = df.copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    bs = [signals_dict[ts] for ts in bt_df.index if ts in signals_dict]
    
    tf = None
    if funding is not None and not funding.empty:
        tf = funding[
            (funding['timestamp'] >= df['timestamp'].iloc[0]) &
            (funding['timestamp'] <= df['timestamp'].iloc[-1])
        ]
        if tf.empty:
            tf = None

    trailing = trailing_cfg.copy()
    trailing['breakeven_at'] = breakeven_at_val

    trades, metrics = run_backtest(
        bt_df, bs,
        initial_balance=10000,
        risk_percent=risk_config.get('risk_percent', 1.0),
        commission=risk_config.get('commission', 0.001),
        slippage=risk_config.get('slippage', 0.0005),
        stop_buffer=risk_config.get('stop_buffer', 0.002),
        breakeven_at=breakeven_at_val,
        trailing_activate=trailing.get('trail_activate', 0.8),
        trailing_step=trailing.get('trail_step', 0.3),
        max_leverage=risk_config.get('max_leverage', 10),
        dynamic_risk=dynamic_risk,
        cooldown_hours=4.0,
        max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
        max_daily_trades=risk_config.get('max_daily_trades', 20),
        max_consecutive_losses=risk_config.get('max_consecutive_losses', 3),
        funding_rates=tf,
    )
    return trades, metrics

def main():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})

    bybit = ccxt.bybit({'enableRateLimit': True})

    eth_cfg = {}
    for asset in config.get('assets', []):
        if asset['symbol'] == 'ETHUSDT' and asset.get('enabled'):
            eth_cfg = asset.get('config', {})
            break

    sig_config = {'strategy': eth_cfg.get('strategy', {}), 'filters': eth_cfg.get('filters', {})}
    gen = SignalGenerator(sig_config, symbol='ETHUSDT')

    df = fetch_ohlcv(bybit, 'ETH/USDT:USDT', timeframe='4h')
    print(f"ETH 4H candles: {len(df)}, {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")
    
    funding = fetch_funding_rates(bybit, 'ETH/USDT:USDT')
    
    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            sig['timestamp'] = str(df['timestamp'].iloc[i])
            signals.append(sig)

    signals_dict = {s['timestamp']: s for s in signals}
    trailing_cfg = eth_cfg.get('trailing', {})

    # Test breakeven_at values
    test_values = [0.3, 0.5, 0.7, 0]
    results = []

    for be_val in test_values:
        label = f"breakeven_at={be_val}" if be_val > 0 else "breakeven=OFF"
        print(f"\n{'='*60}")
        print(f"  Testing: {label}")
        print(f"{'='*60}")
        
        trades, m = run_test(df, signals_dict, funding, be_val, risk_config, dynamic_risk, trailing_cfg)
        
        # Exit reason breakdown
        reasons = {}
        for t in trades:
            r = t['exit_reason']
            if r not in reasons:
                reasons[r] = 0
            reasons[r] += 1
        
        be_count = reasons.get('BREAKEVEN', 0)
        trail_count = reasons.get('TRAILING_STOP', 0)
        sl_count = reasons.get('STOP_LOSS', 0)
        tp_count = reasons.get('TAKE_PROFIT', 0)
        
        print(f"  Trades: {m['total_trades']}")
        print(f"  WR: {m['win_rate']:.1%}")
        print(f"  PF: {m['profit_factor']:.2f}")
        print(f"  Return: {m['total_return']:+.1%}")
        print(f"  DD: {m['max_drawdown']:.1%}")
        print(f"  Breakdown: BE={be_count} TRAIL={trail_count} SL={sl_count} TP={tp_count}")
        
        results.append({
            'label': label,
            'be_val': be_val,
            'trades': m['total_trades'],
            'wr': m['win_rate'],
            'pf': m['profit_factor'],
            'ret': m['total_return'],
            'dd': m['max_drawdown'],
            'be_count': be_count,
            'trail_count': trail_count,
        })

    # Summary
    print(f"\n{'='*60}")
    print(f"  COMPARISON")
    print(f"{'='*60}")
    print(f"  {'Config':<25} {'Trades':>6} {'WR':>6} {'PF':>6} {'Return':>8} {'DD':>6} {'BE':>5} {'TRAIL':>6}")
    print(f"  {'-'*75}")
    for r in results:
        print(f"  {r['label']:<25} {r['trades']:>6} {r['wr']:>5.0%} {r['pf']:>6.2f} {r['ret']:>+7.1%} {r['dd']:>5.1%} {r['be_count']:>5} {r['trail_count']:>6}")

if __name__ == '__main__':
    main()
