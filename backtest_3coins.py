"""12-month honest backtest for TON/GRAM, SUI, APT."""
import sys, time, yaml, ccxt, pandas as pd, numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import run_backtest, calculate_backtest_metrics
from main import SignalGenerator


def fetch_ohlcv(exchange, symbol, timeframe='4h', since_str='2025-08-20T00:00:00Z'):
    all_candles = []
    since = exchange.parse8601(since_str)
    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        except Exception as e:
            print(f"  Fetch error: {e}")
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
        except Exception as e:
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


def run_single(symbol, config, risk_config, dynamic_risk, exchange, since_str='2025-08-20T00:00:00Z'):
    ccxt_sym = f"{symbol.replace('USDT','')}/USDT:USDT"

    print(f"\n{'='*60}")
    print(f"  12-MONTH BACKTEST — {symbol}")
    print(f"{'='*60}")

    print(f"  Fetching 4H candles...")
    df = fetch_ohlcv(exchange, ccxt_sym, since_str=since_str)
    if df.empty:
        print(f"  No data for {symbol}!")
        return None
    print(f"  Got {len(df)} candles: {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
    years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
    print(f"  Period: {years:.1f} years")

    print(f"  Fetching funding rates...")
    funding = fetch_funding_rates(exchange, ccxt_sym)
    print(f"  Got {len(funding)} funding records")

    strat = {**config.get('strategy', {}), **config.get('config', {}).get('strategy', {})}
    trail = {**config.get('trailing', {}), **config.get('config', {}).get('trailing', {})}

    print(f"  Strategy: {strat}")
    print(f"  Trailing: {trail}")

    sig_config = {'strategy': strat, 'filters': {}}
    gen = SignalGenerator(sig_config, symbol=symbol)
    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            sig['timestamp'] = str(df['timestamp'].iloc[i])
            signals.append(sig)
    print(f"  Signals: {len(signals)}")

    if len(signals) < 3:
        print(f"  Too few signals!")
        return None

    bt_df = df.copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]

    test_funding = None
    if funding is not None and not funding.empty:
        test_funding = funding[
            (funding['timestamp'] >= df['timestamp'].iloc[0]) &
            (funding['timestamp'] <= df['timestamp'].iloc[-1])
        ]
        if test_funding.empty:
            test_funding = None

    trades, metrics = run_backtest(
        bt_df, bs,
        initial_balance=10000,
        risk_percent=risk_config.get('risk_percent', 1.0),
        commission=risk_config.get('commission', 0.001),
        slippage=risk_config.get('slippage', 0.0005),
        stop_buffer=risk_config.get('stop_buffer', 0.002),
        breakeven_at=trail.get('breakeven_at', 0.5),
        trailing_activate=trail.get('trail_activate', 1.0),
        trailing_step=trail.get('trail_step', 0.5),
        max_leverage=risk_config.get('max_leverage', 10),
        dynamic_risk=dynamic_risk,
        cooldown_hours=4.0,
        max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
        max_daily_trades=risk_config.get('max_daily_trades', 20),
        max_consecutive_losses=risk_config.get('max_consecutive_losses', 3),
        funding_rates=test_funding,
    )

    print(f"\n  RESULTS — {symbol} ({years:.1f}Y)")
    print(f"  Trades:    {metrics['total_trades']}")
    print(f"  Win Rate:  {metrics['win_rate']:.1%}")
    print(f"  PF:        {metrics['profit_factor']:.2f}")
    print(f"  Return:    {metrics['total_return']:+.1%}")
    print(f"  Max DD:    {metrics['max_drawdown']:.1%}")
    print(f"  Final $:   ${metrics.get('final_balance', 0):,.0f}")

    monthly = {}
    for t in trades:
        et = t.get('entry_time', 'unknown')
        if hasattr(et, 'strftime'):
            month = et.strftime('%Y-%m')
        elif isinstance(et, str):
            month = et[:7]
        else:
            month = 'unknown'
        if month not in monthly:
            monthly[month] = {'pnl': 0, 'trades': 0, 'wins': 0}
        monthly[month]['pnl'] += t.get('pnl', 0)
        monthly[month]['trades'] += 1
        if t.get('pnl', 0) > 0:
            monthly[month]['wins'] += 1

    print(f"\n  Monthly:")
    for m in sorted(monthly.keys()):
        d = monthly[m]
        wr = d['wins']/d['trades']*100 if d['trades'] else 0
        flag = '+' if d['pnl'] >= 0 else ''
        print(f"    {m}: {d['trades']}t WR={wr:.0f}% PnL={flag}${d['pnl']:,.0f}")

    return {
        'symbol': symbol,
        'trades': metrics['total_trades'],
        'wr': metrics['win_rate'],
        'pf': metrics['profit_factor'],
        'ret': metrics['total_return'],
        'dd': metrics['max_drawdown'],
        'final': metrics.get('final_balance', 0),
    }


def main():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})
    exchange = ccxt.bybit({'enableRateLimit': True})

    target_symbols = ['GRAMUSDT', 'SUIUSDT', 'APTUSDT']
    assets = config.get('assets', [])

    results = []
    for symbol in target_symbols:
        asset = None
        for a in assets:
            if a['symbol'] == symbol:
                asset = a
                break
        if not asset:
            print(f"\n{symbol} not found in config!")
            continue

        r = run_single(symbol, asset, risk_config, dynamic_risk, exchange)
        if r:
            results.append(r)

    print(f"\n{'='*60}")
    print(f"  SUMMARY — ALL 3 COINS")
    print(f"{'='*60}")
    print(f"  {'Coin':<10} {'Trades':>6} {'WR':>6} {'PF':>6} {'Ret':>8} {'DD':>6} {'Final$':>10}")
    print(f"  {'-'*54}")
    for r in results:
        print(f"  {r['symbol']:<10} {r['trades']:>6} {r['wr']:>5.1%} {r['pf']:>6.2f} {r['ret']:>+7.1%} {r['dd']:>5.1%} ${r['final']:>9,.0f}")

    if results:
        avg_pf = np.mean([r['pf'] for r in results])
        avg_ret = np.mean([r['ret'] for r in results])
        avg_dd = np.mean([r['dd'] for r in results])
        print(f"  {'AVG':<10} {'':>6} {'':>6} {avg_pf:>6.2f} {avg_ret:>+7.1%} {avg_dd:>5.1%}")


if __name__ == '__main__':
    main()
