"""Quick 12-month ETH backtest with current settings."""
import sys, time, yaml, ccxt, pandas as pd, numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import run_backtest, calculate_backtest_metrics
from main import SignalGenerator


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None):
    all_candles = []
    if since is None:
        since = exchange.parse8601('2025-08-20T00:00:00Z')  # 12 months back
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


def main():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})

    exchange = ccxt.bybit({'enableRateLimit': True})

    symbol = 'ETHUSDT'
    ccxt_sym = 'ETH/USDT:USDT'

    print(f"\n{'='*60}")
    print(f"  12-MONTH BACKTEST — {symbol}")
    print(f"{'='*60}")

    print(f"  Fetching 4H candles...")
    df = fetch_ohlcv(exchange, ccxt_sym)
    if df.empty:
        print("  No data!")
        return
    print(f"  Got {len(df)} candles: {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")

    years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
    print(f"  Period: {years:.1f} years")

    print(f"  Fetching funding rates...")
    funding = fetch_funding_rates(exchange, ccxt_sym)
    print(f"  Got {len(funding)} funding records")

    # Get strategy params for ETH
    assets = config.get('assets', [])
    eth_asset = None
    for a in assets:
        if a['symbol'] == symbol:
            eth_asset = a
            break

    if eth_asset:
        strat = {**config.get('strategy', {}), **eth_asset.get('config', {}).get('strategy', {})}
        trail = {**config.get('trailing', {}), **eth_asset.get('config', {}).get('trailing', {})}
    else:
        strat = config.get('strategy', {})
        trail = config.get('trailing', {})

    print(f"\n  Strategy params: {strat}")
    print(f"  Trailing params: {trail}")

    # Generate signals
    sig_config = {'strategy': strat, 'filters': {}}
    gen = SignalGenerator(sig_config, symbol=symbol)
    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            sig['timestamp'] = str(df['timestamp'].iloc[i])
            signals.append(sig)

    print(f"  Signals generated: {len(signals)}")

    if len(signals) < 3:
        print("  Too few signals!")
        return

    # Run backtest
    bt_df = df.copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]

    # Slice funding for backtest period
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

    print(f"\n{'='*60}")
    print(f"  RESULTS — {symbol} ({years:.1f}Y)")
    print(f"{'='*60}")
    print(f"  Trades:    {metrics['total_trades']}")
    print(f"  Win Rate:  {metrics['win_rate']:.1%}")
    print(f"  PF:        {metrics['profit_factor']:.2f}")
    print(f"  Return:    {metrics['total_return']:+.1%}")
    print(f"  Max DD:    {metrics['max_drawdown']:.1%}")
    print(f"  Final $:   ${metrics.get('final_balance', 0):,.0f}")
    print(f"  Avg R:     {metrics.get('avg_r', 'N/A')}")

    # Monthly breakdown
    if trades:
        print(f"\n  Monthly breakdown:")
        monthly = {}
        for t in trades:
            if isinstance(t.get('entry_time'), str):
                try:
                    month = t['entry_time'][:7]
                except:
                    month = 'unknown'
            elif hasattr(t.get('entry_time'), 'strftime'):
                month = t['entry_time'].strftime('%Y-%m')
            else:
                month = 'unknown'
            if month not in monthly:
                monthly[month] = {'pnl': 0, 'trades': 0, 'wins': 0}
            monthly[month]['pnl'] += t.get('pnl', 0)
            monthly[month]['trades'] += 1
            if t.get('pnl', 0) > 0:
                monthly[month]['wins'] += 1
        for m in sorted(monthly.keys()):
            d = monthly[m]
            wr = d['wins']/d['trades']*100 if d['trades'] else 0
            flag = '+' if d['pnl'] >= 0 else ''
            print(f"    {m}: {d['trades']}t, WR={wr:.0f}%, PnL={flag}${d['pnl']:,.0f}")

    print(f"\n  Trades detail:")
    for t in trades:
        entry_t = t.get('entry_time', '?')
        exit_t = t.get('exit_time', '?')
        if hasattr(entry_t, 'strftime'):
            entry_t = entry_t.strftime('%Y-%m-%d %H:%M')
        if hasattr(exit_t, 'strftime'):
            exit_t = exit_t.strftime('%Y-%m-%d %H:%M')
        entry_t = str(entry_t)[:16]
        exit_t = str(exit_t)[:16]
        side = t.get('side', '?')
        pnl = t.get('pnl', 0)
        flag = '+' if pnl >= 0 else ''
        print(f"    {entry_t} → {exit_t} | {side:5s} | {flag}${pnl:,.0f} | {t.get('exit_reason','')}")


if __name__ == '__main__':
    main()
