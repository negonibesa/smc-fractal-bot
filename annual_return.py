"""Calculate annual return from rolling WF results."""
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
    print(f"ETH 4H candles: {len(df)}, {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")
    years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
    print(f"Period: {years:.1f} years")

    funding = fetch_funding_rates(bybit, 'ETH/USDT:USDT')
    print(f"Funding: {len(funding)}")

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

    m = metrics
    total_return = m['total_return']
    annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
    avg_monthly = total_return / (years * 12) if years > 0 else 0

    print(f"\n{'='*50}")
    print(f"  ETH 4H — FULL BACKTEST RESULTS")
    print(f"{'='*50}")
    print(f"  Period: {years:.1f} years")
    print(f"  Trades: {m['total_trades']}")
    print(f"  Win Rate: {m['win_rate']:.1%}")
    print(f"  Profit Factor: {m['profit_factor']:.2f}")
    print(f"  Total Return: {total_return:+.1%}")
    print(f"  Annual Return: {annual_return:+.1%}")
    print(f"  Avg Monthly: {avg_monthly:+.1%}")
    print(f"  Max Drawdown: {m['max_drawdown']:.1%}")
    print(f"  Final Balance: ${m['final_balance']:,.2f}")

    # GRAM
    gram_cfg = {}
    for asset in config.get('assets', []):
        if asset['symbol'] == 'GRAMUSDT' and asset.get('enabled'):
            gram_cfg = asset.get('config', {})
            break

    sig_config_gram = {'strategy': gram_cfg.get('strategy', {}), 'filters': gram_cfg.get('filters', {})}
    gen_gram = SignalGenerator(sig_config_gram, symbol='GRAMUSDT')

    SPLIT_DATE = '2026-06-15'
    split_ts = pd.Timestamp(SPLIT_DATE)
    binance = ccxt.binance({'enableRateLimit': True})
    df_ton = fetch_ohlcv(binance, 'TON/USDT', timeframe='4h')
    df_gram = fetch_ohlcv(bybit, 'GRAM/USDT:USDT', timeframe='4h')
    df_ton_b = df_ton[df_ton['timestamp'] < split_ts].copy().reset_index(drop=True)
    df_gram_a = df_gram[df_gram['timestamp'] >= split_ts].copy().reset_index(drop=True)
    if not df_ton_b.empty and not df_gram_a.empty:
        df_merged = pd.concat([df_ton_b, df_gram_a], ignore_index=True)
    elif not df_gram_a.empty:
        df_merged = df_gram_a
    else:
        df_merged = df_ton_b
    df_merged = df_merged.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)

    print(f"\n{'='*50}")
    print(f"  GRAM 4H — FULL BACKTEST RESULTS")
    print(f"{'='*50}")
    print(f"  Merged candles: {len(df_merged)}, {df_merged['timestamp'].iloc[0]} -> {df_merged['timestamp'].iloc[-1]}")
    years_g = (df_merged['timestamp'].iloc[-1] - df_merged['timestamp'].iloc[0]).days / 365.25
    print(f"  Period: {years_g:.1f} years")

    funding_g = fetch_funding_rates(bybit, 'GRAM/USDT:USDT')
    print(f"  Funding: {len(funding_g)}")

    signals_g = []
    for i in range(len(df_merged)):
        sig = gen_gram.process_candle(df_merged, i)
        if sig:
            sig['index'] = i
            sig['timestamp'] = str(df_merged['timestamp'].iloc[i])
            signals_g.append(sig)

    bt_df_g = df_merged.copy()
    bt_df_g.index = [str(t) for t in bt_df_g['timestamp']]
    sd_g = {s['timestamp']: s for s in signals_g}
    bs_g = [sd_g[ts] for ts in bt_df_g.index if ts in sd_g]

    tf_g = None
    if funding_g is not None and not funding_g.empty:
        tf_g = funding_g[
            (funding_g['timestamp'] >= df_merged['timestamp'].iloc[0]) &
            (funding_g['timestamp'] <= df_merged['timestamp'].iloc[-1])
        ]
        if tf_g.empty:
            tf_g = None

    trades_g, metrics_g = run_backtest(
        bt_df_g, bs_g,
        initial_balance=10000,
        risk_percent=risk_config.get('risk_percent', 1.0),
        commission=risk_config.get('commission', 0.001),
        slippage=risk_config.get('slippage', 0.0005),
        stop_buffer=risk_config.get('stop_buffer', 0.002),
        breakeven_at=gram_cfg.get('trailing', {}).get('breakeven_at', 0.3),
        trailing_activate=gram_cfg.get('trailing', {}).get('trail_activate', 0.8),
        trailing_step=gram_cfg.get('trailing', {}).get('trail_step', 0.3),
        max_leverage=risk_config.get('max_leverage', 10),
        dynamic_risk=dynamic_risk,
        cooldown_hours=4.0,
        max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
        max_daily_trades=risk_config.get('max_daily_trades', 20),
        max_consecutive_losses=risk_config.get('max_consecutive_losses', 3),
        funding_rates=tf_g,
    )

    mg = metrics_g
    total_return_g = mg['total_return']
    annual_return_g = (1 + total_return_g) ** (1 / years_g) - 1 if years_g > 0 else 0
    avg_monthly_g = total_return_g / (years_g * 12) if years_g > 0 else 0

    print(f"  Trades: {mg['total_trades']}")
    print(f"  Win Rate: {mg['win_rate']:.1%}")
    print(f"  Profit Factor: {mg['profit_factor']:.2f}")
    print(f"  Total Return: {total_return_g:+.1%}")
    print(f"  Annual Return: {annual_return_g:+.1%}")
    print(f"  Avg Monthly: {avg_monthly_g:+.1%}")
    print(f"  Max Drawdown: {mg['max_drawdown']:.1%}")
    print(f"  Final Balance: ${mg['final_balance']:,.2f}")

if __name__ == '__main__':
    main()
