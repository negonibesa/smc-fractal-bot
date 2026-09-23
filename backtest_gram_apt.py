"""GRAM (TON) — fetch TON history from Binance, merge with Bybit GRAM."""
import sys, time, yaml, ccxt, pandas as pd, numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import run_backtest
from main import SignalGenerator

SPLIT_DATE = '2026-06-15'


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None):
    all_candles = []
    if since is None:
        since = exchange.parse8601('2022-01-01T00:00:00Z')
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


def run_segment(df_seg, config, risk_config, dynamic_risk, funding, symbol='GRAMUSDT'):
    strat = config.get('strategy', {})
    trail = config.get('trailing', {})
    sig_config = {'strategy': strat, 'filters': {}}
    gen = SignalGenerator(sig_config, symbol=symbol)
    signals = []
    for i in range(len(df_seg)):
        sig = gen.process_candle(df_seg, i)
        if sig:
            sig['index'] = i
            sig['timestamp'] = str(df_seg['timestamp'].iloc[i])
            signals.append(sig)
    if len(signals) < 3:
        return None
    bt_df = df_seg.copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]
    test_funding = None
    if funding is not None and not funding.empty:
        test_funding = funding[
            (funding['timestamp'] >= df_seg['timestamp'].iloc[0]) &
            (funding['timestamp'] <= df_seg['timestamp'].iloc[-1])
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
    return metrics


def main():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})

    bybit = ccxt.bybit({'enableRateLimit': True})
    binance = ccxt.binance({'enableRateLimit': True})

    split_ts = pd.Timestamp(SPLIT_DATE)

    # ─── GRAM: BINANCE TON + BYBIT GRAM ───
    print(f"\n{'='*60}")
    print(f"  GRAM (TON) — MERGED (Binance TON + Bybit GRAM)")
    print(f"{'='*60}")

    print(f"  Fetching TON/USDT from Binance (4H)...")
    df_ton = fetch_ohlcv(binance, 'TON/USDT')
    if not df_ton.empty:
        df_ton_before = df_ton[df_ton['timestamp'] < split_ts].copy().reset_index(drop=True)
        print(f"  Binance TON before {SPLIT_DATE}: {len(df_ton_before)} candles")
        if not df_ton_before.empty:
            print(f"    {df_ton_before['timestamp'].iloc[0]} to {df_ton_before['timestamp'].iloc[-1]}")
    else:
        df_ton_before = pd.DataFrame()

    print(f"\n  Fetching GRAM/USDT from Bybit (4H)...")
    df_gram = fetch_ohlcv(bybit, 'GRAM/USDT:USDT')
    if not df_gram.empty:
        df_gram_after = df_gram[df_gram['timestamp'] >= split_ts].copy().reset_index(drop=True)
        print(f"  Bybit GRAM after {SPLIT_DATE}: {len(df_gram_after)} candles")
        if not df_gram_after.empty:
            print(f"    {df_gram_after['timestamp'].iloc[0]} to {df_gram_after['timestamp'].iloc[-1]}")
    else:
        df_gram_after = pd.DataFrame()

    if df_ton_before.empty or df_gram_after.empty:
        print(f"  Cannot merge — missing data!")
        return

    # Merge
    df = pd.concat([df_ton_before, df_gram_after], ignore_index=True)
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
    print(f"\n  MERGED: {len(df)} candles, {years:.1f}Y")
    print(f"  {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")

    # 12M backtest
    cutoff = df['timestamp'].iloc[-1] - pd.Timedelta(days=365)
    df_12m = df[df['timestamp'] >= cutoff].copy().reset_index(drop=True)
    print(f"\n  12M: {len(df_12m)} candles ({df_12m['timestamp'].iloc[0]} to {df_12m['timestamp'].iloc[-1]})")

    # Funding from Bybit GRAM (closest proxy)
    print(f"\n  Fetching Bybit GRAM funding...")
    funding = fetch_funding_rates(bybit, 'GRAM/USDT:USDT')
    print(f"  Got {len(funding)} funding records")

    m = run_segment(df_12m, config, risk_config, dynamic_risk, funding)
    if m:
        print(f"\n  12M RESULTS — GRAM (merged)")
        print(f"  Trades: {m['total_trades']}  WR: {m['win_rate']:.1%}  PF: {m['profit_factor']:.2f}  Ret: {m['total_return']:+.1%}  DD: {m['max_drawdown']:.1%}  ${m.get('final_balance',0):,.0f}")

    # Rolling WF on merged data
    print(f"\n  ROLLING WF — GRAM (merged)")
    n = len(df)
    train_pct = 0.4
    step_pct = 0.15
    window_size = int(n * train_pct)
    step_size = int(n * step_pct)

    for i in range(4):
        start = i * step_size
        train_end = start + window_size
        test_start = train_end
        test_end = min(test_start + step_size, n)
        if test_end <= test_start or test_end - test_start < 20 or train_end > n:
            continue
        test_df = df.iloc[test_start:test_end].copy().reset_index(drop=True)
        print(f"\n  Split {i+1}: Test {str(test_df['timestamp'].iloc[0])[:10]} to {str(test_df['timestamp'].iloc[-1])[:10]} ({len(test_df)} candles)")
        m = run_segment(test_df, config, risk_config, dynamic_risk, funding)
        if m:
            pf = m['profit_factor']
            flag = "+" if pf >= 1.2 else ("~" if pf >= 1.0 else "-")
            print(f"    {m['total_trades']}t PF={pf:.2f} WR={m['win_rate']:.0%} Ret={m['total_return']:+.1%} DD={m['max_drawdown']:.1%} {flag}")

    # ─── APT ROLLING WF ───
    print(f"\n\n{'='*60}")
    print(f"  ROLLING WF — APTUSDT")
    print(f"{'='*60}")

    df_apt = fetch_ohlcv(bybit, 'APT/USDT:USDT')
    if df_apt.empty:
        print("  No APT data!")
        return

    years_apt = (df_apt['timestamp'].iloc[-1] - df_apt['timestamp'].iloc[0]).days / 365.25
    print(f"  {len(df_apt)} candles, {years_apt:.1f}Y")

    funding_apt = fetch_funding_rates(bybit, 'APT/USDT:USDT')
    print(f"  Got {len(funding_apt)} funding records")

    n = len(df_apt)
    window_size = int(n * train_pct)
    step_size = int(n * step_pct)

    for i in range(4):
        start = i * step_size
        train_end = start + window_size
        test_start = train_end
        test_end = min(test_start + step_size, n)
        if test_end <= test_start or test_end - test_start < 20 or train_end > n:
            continue
        test_df = df_apt.iloc[test_start:test_end].copy().reset_index(drop=True)
        print(f"\n  Split {i+1}: Test {str(test_df['timestamp'].iloc[0])[:10]} to {str(test_df['timestamp'].iloc[-1])[:10]} ({len(test_df)} candles)")
        m = run_segment(test_df, config, risk_config, dynamic_risk, funding_apt, symbol='APTUSDT')
        if m:
            pf = m['profit_factor']
            flag = "+" if pf >= 1.2 else ("~" if pf >= 1.0 else "-")
            print(f"    {m['total_trades']}t PF={pf:.2f} WR={m['win_rate']:.0%} Ret={m['total_return']:+.1%} DD={m['max_drawdown']:.1%} {flag}")


if __name__ == '__main__':
    main()
