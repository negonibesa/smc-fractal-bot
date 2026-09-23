"""3-Year backtest for live enabled pairs (ETH, GRAM) on 4H candles.
GRAM history = merged Binance TON (pre-split) + Bybit GRAM (post-split).
Usage: python backtest_3y.py
"""
import sys, time, yaml, ccxt, pandas as pd, numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import run_backtest, calculate_backtest_metrics, save_backtest_report

YEARS = 3
SPLIT_DATE = '2026-06-15'


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None, retries=3):
    all_candles = []
    if since is None:
        since = exchange.parse8601('2022-01-01T00:00:00Z')
    while True:
        candles = None
        for attempt in range(retries):
            try:
                candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
                break
            except Exception as e:
                print(f"  Fetch error: {str(e)[:90]}, retrying in {7+attempt*6}s...")
                time.sleep(7 + attempt * 6)
        if not candles:
            print("  Fetch failed, stopping pagination")
            break
        all_candles.extend(candles)
        since = candles[-1][0] + 1
        if len(candles) < 1000:
            break
        time.sleep(0.2)
    if not all_candles:
        return pd.DataFrame()
    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
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
            except Exception as e2:
                print(f"  Funding fetch failed: {e2}")
                break
        if not rates:
            break
        all_rates.extend(rates)
        end_time = rates[0]['timestamp'] - 1
        if len(rates) < 200:
            break
        time.sleep(0.15)
    if not all_rates:
        return pd.DataFrame(columns=['timestamp', 'rate'])
    df = pd.DataFrame([{
        'timestamp': pd.Timestamp(r['timestamp'], unit='ms'),
        'rate': r['fundingRate']
    } for r in all_rates])
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df


def get_pair_config(config, symbol):
    for asset in config.get('assets', []):
        if asset['symbol'] == symbol:
            return asset.get('config', {})
    return {}


def run_segment(df, pair_config, config, risk_config, dynamic_risk, funding, symbol):
    strat = {**config.get('strategy', {}), **pair_config.get('strategy', {})}
    trail = {**config.get('trailing', {}), **pair_config.get('trailing', {})}
    filters = {**config.get('filters', {}), **pair_config.get('filters', {})}

    sig_config = {'strategy': strat, 'filters': filters}
    from main import SignalGenerator
    gen = SignalGenerator(sig_config, symbol=symbol)

    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            sig['timestamp'] = str(df['timestamp'].iloc[i])
            signals.append(sig)

    print(f"  Signals: {len(signals)}")
    if not signals:
        return None

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
        breakeven_at=trail.get('breakeven_at', 0.5),
        trailing_activate=trail.get('trail_activate', 1.0),
        trailing_step=trail.get('trail_step', 0.5),
        max_leverage=risk_config.get('max_leverage', 10),
        dynamic_risk=dynamic_risk,
        cooldown_hours=4.0,
        max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
        max_daily_trades=risk_config.get('max_daily_trades', 20),
        max_consecutive_losses=risk_config.get('max_consecutive_losses', 3),
        funding_rates=tf,
    )
    return trades, metrics


def print_results(symbol, df, trades, metrics, out_path):
    years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
    print(f"\n  RESULTS — {symbol} ({years:.1f}Y)")
    print(f"  Trades:    {metrics['total_trades']}")
    print(f"  Win Rate:  {metrics['win_rate']:.1%}")
    print(f"  PF:        {metrics['profit_factor']:.2f}")
    print(f"  Return:    {metrics['total_return']:+.1%}")
    print(f"  Max DD:    {metrics['max_drawdown']:.1%}")
    print(f"  Final $:   ${metrics.get('final_balance', 0):,.0f}")

    if trades:
        yearly = {}
        for t in trades:
            et = t['entry_time']
            if isinstance(et, str):
                try:
                    et = pd.Timestamp(et)
                except:
                    continue
            y = et.year
            if y not in yearly:
                yearly[y] = {'trades': 0, 'wins': 0, 'pnl': 0}
            yearly[y]['trades'] += 1
            yearly[y]['pnl'] += t['pnl']
            if t['pnl'] > 0:
                yearly[y]['wins'] += 1

        print(f"\n  YEARLY:")
        for y in sorted(yearly.keys()):
            d = yearly[y]
            yt = [t for t in trades if isinstance(t['entry_time'], str) or True]
            year_trades = [t for t in trades if str(t['entry_time'])[:4] == str(y)]
            wins = sum(1 for t in year_trades if t['pnl'] > 0)
            wpnl = sum(t['pnl'] for t in year_trades if t['pnl'] > 0)
            lpnl = abs(sum(t['pnl'] for t in year_trades if t['pnl'] <= 0))
            pf = wpnl / lpnl if lpnl > 0 else (float('inf') if wpnl > 0 else 0)
            print(f"    {y}: {len(year_trades)}t, WR={wins/max(len(year_trades),1):.0%}, "
                  f"PF={pf:.2f}, PnL=${sum(t['pnl'] for t in year_trades):+,.0f}")

        print(f"\n  Monthly PnL (last 12):")
        monthly = {}
        for t in trades:
            et = t['entry_time']
            if hasattr(et, 'strftime'):
                month = et.strftime('%Y-%m')
            else:
                month = str(et)[:7]
            monthly.setdefault(month, 0)
            monthly[month] += t['pnl']
        for m in sorted(monthly.keys())[-12:]:
            flag = '+' if monthly[m] >= 0 else ''
            print(f"    {m}: {flag}${monthly[m]:,.0f}")

    save_backtest_report(metrics, trades, out_path)
    print(f"  Report saved: {out_path}")
    return years


def main():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})

    bybit = ccxt.bybit({'enableRateLimit': True})
    bybit.options['defaultType'] = 'linear'
    binance = ccxt.binance({'enableRateLimit': True})

    results_dir = Path(__file__).parent / "backtest_results"
    results_dir.mkdir(exist_ok=True)

    since_ms = bybit.parse8601(f"{pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=365*YEARS):%Y-%m-%dT%H:%M:%SZ}")

    # ─── ETHUSDT ───
    symbol = 'ETHUSDT'
    print(f"\n{'='*68}")
    print(f"  {YEARS}-YEAR BACKTEST — {symbol} (4H)")
    print(f"{'='*68}")
    df = fetch_ohlcv(bybit, 'ETH/USDT:USDT', since=since_ms)
    if df.empty:
        print("  No data!")
        return
    print(f"  Candles: {len(df)}, {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
    years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
    print(f"  Period: {years:.1f} years")

    print("  Fetching funding rates...")
    funding = fetch_funding_rates(bybit, 'ETH/USDT:USDT')
    print(f"  Funding records: {len(funding)}")

    pair_config = get_pair_config(config, symbol)
    res = run_segment(df, pair_config, config, risk_config, dynamic_risk, funding, symbol)
    if res:
        trades, metrics = res
        print_results(symbol, df, trades, metrics, str(results_dir / f"{symbol}_3y.txt"))

    # ─── GRAMUSDT (TON merge) ───
    symbol = 'GRAMUSDT'
    print(f"\n{'='*68}")
    print(f"  {YEARS}-YEAR BACKTEST — {symbol} (4H, merged TON+GRAM)")
    print(f"{'='*68}")
    split_ts = pd.Timestamp(SPLIT_DATE)
    df_ton = fetch_ohlcv(binance, 'TON/USDT', since=since_ms)
    df_gram = fetch_ohlcv(bybit, 'GRAM/USDT:USDT', since=since_ms)

    df_ton_b = df_ton[df_ton['timestamp'] < split_ts].copy().reset_index(drop=True) if not df_ton.empty else pd.DataFrame()
    df_gram_a = df_gram[df_gram['timestamp'] >= split_ts].copy().reset_index(drop=True) if not df_gram.empty else pd.DataFrame()
    print(f"  TON (Binance) before {SPLIT_DATE}: {len(df_ton_b)}c")
    print(f"  GRAM (Bybit) after {SPLIT_DATE}: {len(df_gram_a)}c")

    if not df_ton_b.empty and not df_gram_a.empty:
        df = pd.concat([df_ton_b, df_gram_a], ignore_index=True)
    elif not df_gram_a.empty:
        df = df_gram_a
    else:
        df = df_ton_b
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)

    if df.empty:
        print("  No data!")
        return
    print(f"  Candles: {len(df)}, {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
    years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
    print(f"  Period: {years:.1f} years")

    print("  Fetching funding rates...")
    funding = fetch_funding_rates(bybit, 'GRAM/USDT:USDT')
    print(f"  Funding records: {len(funding)}")

    pair_config = get_pair_config(config, symbol)
    res = run_segment(df, pair_config, config, risk_config, dynamic_risk, funding, symbol)
    if res:
        trades, metrics = res
        print_results(symbol, df, trades, metrics, str(results_dir / f"{symbol}_3y.txt"))


if __name__ == '__main__':
    main()