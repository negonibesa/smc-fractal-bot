"""Combined Rolling WF: GRAM(merged), APT, TRX, MNT, CRO, AVAX, XLM, XMR."""
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


def run_segment(df_seg, strat, trail, risk_config, dynamic_risk, funding, symbol):
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
    tf = None
    if funding is not None and not funding.empty:
        tf = funding[
            (funding['timestamp'] >= df_seg['timestamp'].iloc[0]) &
            (funding['timestamp'] <= df_seg['timestamp'].iloc[-1])
        ]
        if tf.empty:
            tf = None
    trades, metrics = run_backtest(
        bt_df, bs, initial_balance=10000,
        risk_percent=risk_config.get('risk_percent', 1.0),
        commission=risk_config.get('commission', 0.001),
        slippage=risk_config.get('slippage', 0.0005),
        stop_buffer=risk_config.get('stop_buffer', 0.002),
        breakeven_at=trail.get('breakeven_at', 0.5),
        trailing_activate=trail.get('trail_activate', 1.0),
        trailing_step=trail.get('trail_step', 0.5),
        max_leverage=risk_config.get('max_leverage', 10),
        dynamic_risk=dynamic_risk, cooldown_hours=4.0,
        max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
        max_daily_trades=risk_config.get('max_daily_trades', 20),
        max_consecutive_losses=risk_config.get('max_consecutive_losses', 3),
        funding_rates=tf,
    )
    return metrics


def rolling_wf(df, strat, trail, risk_config, dynamic_risk, funding, symbol, n_splits=4):
    n = len(df)
    train_pct = 0.4
    step_pct = 0.15
    window_size = int(n * train_pct)
    step_size = int(n * step_pct)
    results = []

    for i in range(n_splits):
        start = i * step_size
        train_end = start + window_size
        test_start = train_end
        test_end = min(test_start + step_size, n)
        if test_end <= test_start or test_end - test_start < 20 or train_end > n:
            continue
        test_df = df.iloc[test_start:test_end].copy().reset_index(drop=True)
        ts = str(test_df['timestamp'].iloc[0])[:10]
        te = str(test_df['timestamp'].iloc[-1])[:10]
        m = run_segment(test_df, strat, trail, risk_config, dynamic_risk, funding, symbol)
        if m:
            pf = m['profit_factor']
            flag = "+" if pf >= 1.2 else ("~" if pf >= 1.0 else "-")
            print(f"    Split {i+1}: {ts}→{te} ({len(test_df)}c) {m['total_trades']}t PF={pf:.2f} WR={m['win_rate']:.0%} Ret={m['total_return']:+.1%} DD={m['max_drawdown']:.1%} {flag}")
            results.append(pf)
        else:
            print(f"    Split {i+1}: {ts}→{te} — no trades")

    if results:
        avg = np.mean(results)
        mn = min(results)
        print(f"    AVG PF={avg:.2f}  MIN PF={mn:.2f}")
        return avg, mn
    return None, None


def main():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})
    strat = config.get('strategy', {})
    trail = config.get('trailing', {})

    bybit = ccxt.bybit({'enableRateLimit': True})
    binance = ccxt.binance({'enableRateLimit': True})

    all_results = {}

    # ─── GRAM (MERGED: Binance TON + Bybit GRAM) ───
    print(f"\n{'='*60}")
    print(f"  GRAM (TON) — MERGED ROLLING WF")
    print(f"{'='*60}")

    split_ts = pd.Timestamp(SPLIT_DATE)
    df_ton = fetch_ohlcv(binance, 'TON/USDT')
    df_gram = fetch_ohlcv(bybit, 'GRAM/USDT:USDT')

    if not df_ton.empty and not df_gram.empty:
        df_ton_b = df_ton[df_ton['timestamp'] < split_ts].copy().reset_index(drop=True)
        df_gram_a = df_gram[df_gram['timestamp'] >= split_ts].copy().reset_index(drop=True)
        print(f"  TON (Binance) before {SPLIT_DATE}: {len(df_ton_b)}c")
        print(f"  GRAM (Bybit) after {SPLIT_DATE}: {len(df_gram_a)}c")

        df = pd.concat([df_ton_b, df_gram_a], ignore_index=True)
        df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
        years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
        print(f"  Merged: {len(df)}c, {years:.1f}Y")

        funding = fetch_funding_rates(bybit, 'GRAM/USDT:USDT')
        print(f"  Funding: {len(funding)} records")

        avg, mn = rolling_wf(df, strat, trail, risk_config, dynamic_risk, funding, 'GRAMUSDT')
        all_results['GRAM'] = {'avg': avg, 'min': mn}
    else:
        print(f"  No data!")

    # ─── APT ───
    print(f"\n{'='*60}")
    print(f"  APTUSDT — ROLLING WF")
    print(f"{'='*60}")

    df_apt = fetch_ohlcv(bybit, 'APT/USDT:USDT')
    if not df_apt.empty:
        years = (df_apt['timestamp'].iloc[-1] - df_apt['timestamp'].iloc[0]).days / 365.25
        print(f"  {len(df_apt)}c, {years:.1f}Y")
        funding = fetch_funding_rates(bybit, 'APT/USDT:USDT')
        print(f"  Funding: {len(funding)} records")
        avg, mn = rolling_wf(df_apt, strat, trail, risk_config, dynamic_risk, funding, 'APTUSDT')
        all_results['APT'] = {'avg': avg, 'min': mn}
    else:
        print(f"  No data!")

    # ─── 6 NEW COINS ───
    coins = [
        ('TRXUSDT', 'TRX/USDT:USDT'),
        ('MNTUSDT', 'MNT/USDT:USDT'),
        ('CROUSDT', 'CRO/USDT:USDT'),
        ('AVAXUSDT', 'AVAX/USDT:USDT'),
        ('XLMUSDT', 'XLM/USDT:USDT'),
        ('XMRUSDT', 'XMR/USDT:USDT'),
    ]

    for sym, ccxt_sym in coins:
        print(f"\n{'='*60}")
        print(f"  {sym} — ROLLING WF")
        print(f"{'='*60}")

        df = fetch_ohlcv(bybit, ccxt_sym)
        if df.empty:
            print(f"  No data!")
            continue

        years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
        print(f"  {len(df)}c, {years:.1f}Y")

        funding = fetch_funding_rates(bybit, ccxt_sym)
        print(f"  Funding: {len(funding)} records")

        avg, mn = rolling_wf(df, strat, trail, risk_config, dynamic_risk, funding, sym)
        all_results[sym] = {'avg': avg, 'min': mn}

    # ─── FINAL SUMMARY ───
    print(f"\n\n{'='*70}")
    print(f"  FINAL SUMMARY — ALL COINS")
    print(f"{'='*70}")
    print(f"  {'Coin':<10} {'AVG PF':>7} {'MIN PF':>7} {'Verdict':>10}")
    print(f"  {'-'*35}")

    sorted_r = sorted(all_results.items(), key=lambda x: x[1]['avg'] or 0, reverse=True)
    for sym, r in sorted_r:
        avg = r['avg']
        mn = r['min']
        if avg is None:
            verdict = "NO DATA"
        elif avg >= 1.2 and mn >= 1.0:
            verdict = "KEEP"
        elif avg >= 1.0:
            verdict = "WEAK"
        else:
            verdict = "DEAD"
        avg_s = f"{avg:.2f}" if avg else "—"
        mn_s = f"{mn:.2f}" if mn else "—"
        print(f"  {sym:<10} {avg_s:>7} {mn_s:>7} {verdict:>10}")


if __name__ == '__main__':
    main()
