"""Rolling Walk-Forward on 1D candles for ETH + GRAM."""
import sys, time, yaml, itertools, logging, ccxt, pandas as pd, numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import run_backtest, calculate_backtest_metrics
from main import SignalGenerator

logging.basicConfig(level=logging.WARNING)

CANDLE_SECONDS = 24 * 3600  # 1D
COOLDOWN_HOURS = 24.0

PARAM_RANGES = {
    'lookback': [8, 10, 12, 15],
    'sweep_threshold': [0.005, 0.008, 0.010, 0.012],
    'center_proximity': [0.008, 0.010, 0.012, 0.015],
    'tp_multiplier': [1.0, 1.5, 2.0],
    'timeout': [2, 3, 5],
}
TRAILING_RANGES = {
    'breakeven_at': [0.3, 0.5],
    'trail_activate': [0.8, 1.0],
    'trail_step': [0.3, 0.5],
}

SPLIT_DATE = '2026-06-15'


def fetch_ohlcv(exchange, symbol, timeframe='1d', since=None):
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


def run_bt_segment(df, params, trailing, risk_config, dynamic_risk, funding=None, symbol='BT'):
    sig_config = {'strategy': params, 'filters': {}}
    gen = SignalGenerator(sig_config, symbol=symbol)
    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            sig['timestamp'] = str(df['timestamp'].iloc[i])
            signals.append(sig)
    if len(signals) < 3:
        return None, []
    bt_df = df.copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    sd = {s['timestamp']: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]
    if not bs:
        return None, []
    trades, metrics = run_backtest(
        bt_df, bs,
        initial_balance=10000,
        risk_percent=risk_config.get('risk_percent', 1.0),
        commission=risk_config.get('commission', 0.001),
        slippage=risk_config.get('slippage', 0.0005),
        stop_buffer=risk_config.get('stop_buffer', 0.002),
        breakeven_at=trailing.get('breakeven_at', 0.5),
        trailing_activate=trailing.get('trail_activate', 1.0),
        trailing_step=trailing.get('trail_step', 0.5),
        max_leverage=risk_config.get('max_leverage', 10),
        dynamic_risk=dynamic_risk,
        cooldown_hours=COOLDOWN_HOURS,
        max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
        max_daily_trades=risk_config.get('max_daily_trades', 20),
        max_consecutive_losses=3,
        funding_rates=funding,
    )
    return metrics, trades


def generate_combos(ranges, max_n=30):
    keys = list(ranges.keys())
    values = [ranges[k] for k in keys]
    combos = [dict(zip(keys, c)) for c in itertools.product(*values)]
    if len(combos) > max_n:
        idx = np.linspace(0, len(combos)-1, max_n, dtype=int)
        combos = [combos[i] for i in idx]
    return combos


def optimize_on_train(df_train, current_params, current_trailing, risk_config, dynamic_risk, symbol='BT'):
    combos = generate_combos(PARAM_RANGES, max_n=20)
    best_pf = 0
    best_params = None
    best_trailing = current_trailing.copy()
    n = len(df_train)
    split_size = n // 3

    for combo in combos:
        test_params = {**current_params, **combo}
        oos_results = []
        for s in range(1, 3):
            seg_start = s * split_size
            seg_end = min((s + 1) * split_size, n)
            seg = df_train.iloc[seg_start:seg_end].copy().reset_index(drop=True)
            if len(seg) < 15:
                continue
            m, _ = run_bt_segment(seg, test_params, current_trailing, risk_config, dynamic_risk, symbol=symbol)
            if m and m['total_trades'] >= 2:
                oos_results.append(m['profit_factor'])
        if not oos_results:
            continue
        avg_pf = np.mean(oos_results)
        min_pf = min(oos_results)
        if avg_pf > best_pf and min_pf >= 1.0:
            best_pf = avg_pf
            best_params = test_params

    if best_params is None:
        return current_params, current_trailing, 0

    for tc in generate_combos(TRAILING_RANGES, max_n=8):
        test_trailing = {**current_trailing, **tc}
        oos_results = []
        for s in range(1, 3):
            seg_start = s * split_size
            seg_end = min((s + 1) * split_size, n)
            seg = df_train.iloc[seg_start:seg_end].copy().reset_index(drop=True)
            if len(seg) < 15:
                continue
            m, _ = run_bt_segment(seg, best_params, test_trailing, risk_config, dynamic_risk, symbol=symbol)
            if m and m['total_trades'] >= 2:
                oos_results.append(m['profit_factor'])
        if oos_results:
            avg_pf = np.mean(oos_results)
            if avg_pf > best_pf and min(oos_results) >= 1.0:
                best_pf = avg_pf
                best_trailing = test_trailing

    return best_params, best_trailing, best_pf


def rolling_wf(df, config, risk_config, dynamic_risk, symbol, funding_rates,
               train_pct=0.5, step_pct=0.2, n_splits=4):
    n = len(df)
    window_size = int(n * train_pct)
    step_size = int(n * step_pct)
    current_params = config.get('strategy', {})
    current_trailing = config.get('trailing', {})
    results = []

    for i in range(n_splits):
        start = i * step_size
        train_end = start + window_size
        test_start = train_end
        test_end = min(test_start + step_size, n)

        if test_end <= test_start or test_end - test_start < 15 or train_end > n:
            continue

        train_df = df.iloc[start:train_end].copy().reset_index(drop=True)
        test_df = df.iloc[test_start:test_end].copy().reset_index(drop=True)

        test_funding = None
        if funding_rates is not None and not funding_rates.empty:
            test_funding = funding_rates[
                (funding_rates['timestamp'] >= test_df['timestamp'].iloc[0]) &
                (funding_rates['timestamp'] <= test_df['timestamp'].iloc[-1])
            ]
            if test_funding.empty:
                test_funding = None

        print(f"\n  Split {i+1}:")
        print(f"    Train: {str(train_df['timestamp'].iloc[0])[:10]} to {str(train_df['timestamp'].iloc[-1])[:10]} ({len(train_df)} candles)")
        print(f"    Test:  {str(test_df['timestamp'].iloc[0])[:10]} to {str(test_df['timestamp'].iloc[-1])[:10]} ({len(test_df)} candles)")

        t0 = time.time()
        opt_params, opt_trailing, opt_pf = optimize_on_train(
            train_df, current_params, current_trailing, risk_config, dynamic_risk, symbol=symbol
        )
        elapsed = time.time() - t0
        print(f"    Optimize: {elapsed:.0f}s, train PF={opt_pf:.2f}")

        if opt_pf == 0:
            opt_params = current_params
            opt_trailing = current_trailing

        m, trades = run_bt_segment(test_df, opt_params, opt_trailing, risk_config, dynamic_risk,
                                    funding=test_funding, symbol=symbol)

        if m is None:
            print(f"    No trades on OOS")
            continue

        pf = m['profit_factor']
        wr = m['win_rate']
        ret = m['total_return']
        dd = m['max_drawdown']
        nt = m['total_trades']
        flag = "+" if pf >= 1.2 else ("~" if pf >= 1.0 else "-")
        print(f"    OOS: {nt}t PF={pf:.2f} WR={wr:.0%} Ret={ret:+.1%} DD={dd:.1%} {flag}")

        results.append({
            'split': i+1,
            'test_period': f"{str(test_df['timestamp'].iloc[0])[:10]} to {str(test_df['timestamp'].iloc[-1])[:10]}",
            'candles': len(test_df),
            'trades': nt, 'pf': pf, 'wr': wr, 'ret': ret, 'dd': dd,
        })

        current_params = opt_params
        current_trailing = opt_trailing

    return results


def main():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})

    bybit = ccxt.bybit({'enableRateLimit': True})
    binance = ccxt.binance({'enableRateLimit': True})

    all_results = {}

    # ETH 1D
    print("=" * 60)
    print("  ETHUSDT — Rolling WF on 1D")
    print("=" * 60)

    eth_cfg = {}
    for asset in config.get('assets', []):
        if asset['symbol'] == 'ETHUSDT':
            eth_cfg = asset.get('config', {})
            break
    eth_cfg['strategy'] = eth_cfg.get('strategy', {})
    eth_cfg['strategy']['timeout'] = 3

    df_eth = fetch_ohlcv(bybit, 'ETH/USDT:USDT', timeframe='1d')
    print(f"  Candles: {len(df_eth)}")
    funding_eth = fetch_funding_rates(bybit, 'ETH/USDT:USDT')
    print(f"  Funding: {len(funding_eth)}")

    r_eth = rolling_wf(df_eth, eth_cfg, risk_config, dynamic_risk, 'ETHUSDT', funding_eth,
                        train_pct=0.5, step_pct=0.2, n_splits=4)
    all_results['ETH'] = r_eth

    # GRAM 1D
    print(f"\n{'=' * 60}")
    print("  GRAMUSDT — Rolling WF on 1D (merged TON+GRAM)")
    print("=" * 60)

    gram_cfg = {}
    for asset in config.get('assets', []):
        if asset['symbol'] == 'GRAMUSDT':
            gram_cfg = asset.get('config', {})
            break
    gram_cfg['strategy'] = gram_cfg.get('strategy', {})
    gram_cfg['strategy']['timeout'] = 3

    split_ts = pd.Timestamp(SPLIT_DATE)
    df_ton = fetch_ohlcv(binance, 'TON/USDT', timeframe='1d')
    df_gram = fetch_ohlcv(bybit, 'GRAM/USDT:USDT', timeframe='1d')
    df_ton_b = df_ton[df_ton['timestamp'] < split_ts].copy().reset_index(drop=True)
    df_gram_a = df_gram[df_gram['timestamp'] >= split_ts].copy().reset_index(drop=True)
    if not df_ton_b.empty and not df_gram_a.empty:
        df_gram_merged = pd.concat([df_ton_b, df_gram_a], ignore_index=True)
    elif not df_gram_a.empty:
        df_gram_merged = df_gram_a
    else:
        df_gram_merged = df_ton_b
    df_gram_merged = df_gram_merged.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    print(f"  Merged candles: {len(df_gram_merged)}")
    funding_gram = fetch_funding_rates(bybit, 'GRAM/USDT:USDT')
    print(f"  Funding: {len(funding_gram)}")

    r_gram = rolling_wf(df_gram_merged, gram_cfg, risk_config, dynamic_risk, 'GRAMUSDT', funding_gram,
                         train_pct=0.5, step_pct=0.2, n_splits=3)
    all_results['GRAM'] = r_gram

    # Summary
    print(f"\n{'=' * 60}")
    print("  SUMMARY")
    print("=" * 60)
    for coin, results in all_results.items():
        if not results:
            print(f"  {coin}: No results")
            continue
        pfs = [r['pf'] for r in results]
        rets = [r['ret'] for r in results]
        dds = [r['dd'] for r in results]
        print(f"\n  {coin}:")
        for r in results:
            flag = "+" if r['pf'] >= 1.2 else ("~" if r['pf'] >= 1.0 else "-")
            print(f"    Split {r['split']}: {r['trades']}t PF={r['pf']:.2f} Ret={r['ret']:+.1%} DD={r['dd']:.1%} {flag}")
        print(f"    AVG PF={np.mean(pfs):.2f}  MIN PF={np.min(pfs):.2f}  AVG Ret={np.mean(rets):+.1%}")


if __name__ == '__main__':
    main()
