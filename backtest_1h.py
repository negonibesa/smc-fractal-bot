"""3-month backtest ETH + GRAM on 1H candles."""
import sys, time, yaml, ccxt, pandas as pd, numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import run_backtest
from main import SignalGenerator

SPLIT_DATE = '2026-06-15'
INTERVAL = '1h'


def fetch_ohlcv(exchange, symbol, timeframe='1h', since=None):
    all_candles = []
    if since is None:
        since = exchange.parse8601('2022-01-01T00:00:00Z')
    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        except Exception as e:
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


def run_bt(df, sig_config, risk_config, dynamic_risk, funding, symbol):
    gen = SignalGenerator(sig_config, symbol=symbol)
    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            sig['timestamp'] = str(df['timestamp'].iloc[i])
            signals.append(sig)

    if len(signals) < 3:
        print(f"  Only {len(signals)} signals — not enough")
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
        bt_df, bs, initial_balance=10000,
        risk_percent=risk_config.get('risk_percent', 1.0),
        commission=risk_config.get('commission', 0.001),
        slippage=risk_config.get('slippage', 0.0005),
        stop_buffer=risk_config.get('stop_buffer', 0.002),
        breakeven_at=0.3,
        trailing_activate=0.8,
        trailing_step=0.3,
        max_leverage=risk_config.get('max_leverage', 10),
        dynamic_risk=dynamic_risk,
        cooldown_hours=1.0,  # 1H cooldown for 1H candles
        max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
        max_daily_trades=risk_config.get('max_daily_trades', 20),
        max_consecutive_losses=risk_config.get('max_consecutive_losses', 3),
        funding_rates=tf,
    )

    m = metrics
    print(f"  Trades: {m['total_trades']}")
    print(f"  Win Rate: {m['win_rate']:.1%}")
    print(f"  Profit Factor: {m['profit_factor']:.2f}")
    print(f"  Total Return: {m['total_return']:+.1%}")
    print(f"  Max Drawdown: {m['max_drawdown']:.1%}")
    print(f"  Avg Win $: {np.mean([t['pnl'] for t in trades if t['pnl'] > 0]):.2f}" if any(t['pnl'] > 0 for t in trades) else "")
    print(f"  Avg Loss $: {np.mean([t['pnl'] for t in trades if t['pnl'] <= 0]):.2f}" if any(t['pnl'] <= 0 for t in trades) else "")
    print(f"  Total Funding: ${m.get('total_funding', 0):.2f}")

    win = [t for t in trades if t['pnl'] > 0]
    lose = [t for t in trades if t['pnl'] <= 0]
    print(f"  Winners: {len(win)}, Losers: {len(lose)}")

    # Trade list
    if trades:
        print(f"\n  #  | {'Date':>16} | {'Side':>4} | Entry    | Exit     | PnL      | Reason")
        print(f"  {'-'*80}")
        for i, t in enumerate(trades):
            ts = str(t.get('entry_time', ''))[:16]
            side = t.get('direction', '?')
            entry = t.get('entry_price', 0)
            exit_p = t.get('exit_price', 0)
            pnl = t.get('pnl', 0)
            reason = t.get('exit_reason', '?')
            marker = '+' if pnl > 0 else '-'
            print(f"  {i+1:>3} | {ts:>16} | {side:>4} | {entry:>8.2f} | {exit_p:>8.2f} | {pnl:>+8.2f} | {reason} {marker}")

    return metrics


def main():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})
    default_strat = config.get('strategy', {})
    default_strat['timeout'] = 6  # 6 candles * 1H = 6h timeout (was 15 * 4H = 60h)

    bybit = ccxt.bybit({'enableRateLimit': True})
    binance = ccxt.binance({'enableRateLimit': True})

    three_months_ago = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=90)
    three_months_ago_ms = int(three_months_ago.timestamp() * 1000)

    # ─── ETH on 1H ───
    print("=" * 60)
    print("  ETHUSDT — 3 MONTHS ON 1H CANDLES")
    print("=" * 60)

    eth_cfg = {}
    for asset in config.get('assets', []):
        if asset['symbol'] == 'ETHUSDT' and asset.get('enabled', True):
            eth_cfg = asset.get('config', {})
            break

    eth_strat = eth_cfg.get('strategy', default_strat)
    eth_strat['timeout'] = 6  # 6 * 1H = 6h
    eth_filters = eth_cfg.get('filters', {})

    sig_config = {'strategy': eth_strat, 'filters': eth_filters}

    df_eth = fetch_ohlcv(bybit, 'ETH/USDT:USDT', timeframe=INTERVAL, since=three_months_ago_ms)
    print(f"  Candles: {len(df_eth)}, {df_eth['timestamp'].iloc[0]} → {df_eth['timestamp'].iloc[-1]}")

    funding_eth = fetch_funding_rates(bybit, 'ETH/USDT:USDT')
    print(f"  Funding records: {len(funding_eth)}")

    run_bt(df_eth, sig_config, risk_config, dynamic_risk, funding_eth, 'ETHUSDT')

    # ─── GRAM on 1H ───
    print(f"\n{'=' * 60}")
    print("  GRAMUSDT — 3 MONTHS ON 1H CANDLES (merged TON+GRAM)")
    print("=" * 60)

    gram_cfg = {}
    for asset in config.get('assets', []):
        if asset['symbol'] == 'GRAMUSDT':
            gram_cfg = asset.get('config', {})
            break

    gram_strat = gram_cfg.get('strategy', default_strat)
    gram_strat['timeout'] = 6  # 6 * 1H = 6h
    gram_filters = gram_cfg.get('filters', {})

    sig_config_gram = {'strategy': gram_strat, 'filters': gram_filters}

    split_ts = pd.Timestamp(SPLIT_DATE)
    df_ton = fetch_ohlcv(binance, 'TON/USDT', timeframe=INTERVAL, since=three_months_ago_ms)
    df_gram = fetch_ohlcv(bybit, 'GRAM/USDT:USDT', timeframe=INTERVAL, since=three_months_ago_ms)

    print(f"  TON (Binance) before {SPLIT_DATE}: {len(df_ton)}c")
    print(f"  GRAM (Bybit) after {SPLIT_DATE}: {len(df_gram)}c")

    df_ton_b = df_ton[df_ton['timestamp'] < split_ts].copy().reset_index(drop=True)
    df_gram_a = df_gram[df_gram['timestamp'] >= split_ts].copy().reset_index(drop=True)

    if not df_ton_b.empty and not df_gram_a.empty:
        df_gram_merged = pd.concat([df_ton_b, df_gram_a], ignore_index=True)
    elif not df_gram_a.empty:
        df_gram_merged = df_gram_a
    else:
        df_gram_merged = df_ton_b

    df_gram_merged = df_gram_merged.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    print(f"  Merged candles: {len(df_gram_merged)}, {df_gram_merged['timestamp'].iloc[0]} → {df_gram_merged['timestamp'].iloc[-1]}")

    funding_gram = fetch_funding_rates(bybit, 'GRAM/USDT:USDT')
    print(f"  Funding records: {len(funding_gram)}")

    run_bt(df_gram_merged, sig_config_gram, risk_config, dynamic_risk, funding_gram, 'GRAMUSDT')


if __name__ == '__main__':
    main()
