"""
2x2 Matrix A/B test:
Entry: H/L center vs Body center
Exit: r_multiple vs structure
4 cells to isolate effects
"""
import sys
import time
import yaml
import ccxt
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backtest import run_backtest
from smc_features import find_consolidation_center
from main import SignalGenerator


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None, limit=1000):
    all_candles = []
    if since is None:
        since = exchange.parse8601('2022-01-01T00:00:00Z')
    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        except Exception as e:
            print(f"  Fetch error: {e}, retrying...")
            time.sleep(5)
            try:
                candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            except:
                break
        if not candles:
            break
        all_candles.extend(candles)
        since = candles[-1][0] + 1
        if len(candles) < limit:
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
        return pd.DataFrame(columns=['timestamp', 'rate'])
    df = pd.DataFrame([{
        'timestamp': pd.Timestamp(r['timestamp'], unit='ms'),
        'rate': r['fundingRate']
    } for r in all_rates])
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df


def run_one_cell(df, funding, config, center_mode, tp_mode, risk_config, dynamic_risk, trailing, symbol):
    """Run one cell of the 2x2 matrix."""
    # Monkey-patch find_consolidation_center in BOTH smc_features and main modules
    import smc_features as sf
    import main as main_mod
    original_find_sf = sf.find_consolidation_center
    original_find_main = main_mod.find_consolidation_center

    def patched_find(df_inner, lookback=20, use_body=True):
        return original_find_sf(df_inner, lookback=lookback, use_body=(center_mode == 'body'))

    sf.find_consolidation_center = patched_find
    main_mod.find_consolidation_center = patched_find

    try:
        test_config = {
            'strategy': {**config['strategy'], 'tp_mode': tp_mode},
            'filters': config.get('filters', {}),
        }

        gen = SignalGenerator(test_config, symbol=symbol)
        signals = []
        for i in range(len(df)):
            sig = gen.process_candle(df, i)
            if sig:
                sig['index'] = i
                sig['timestamp'] = str(df['timestamp'].iloc[i])
                signals.append(sig)

        if len(signals) < 5:
            sf.find_consolidation_center = original_find
            return None

        bt_df = df.copy()
        bt_df.index = [str(t) for t in bt_df['timestamp']]
        sd = {s['timestamp']: s for s in signals}
        bs = [sd[ts] for ts in bt_df.index if ts in sd]

        trades, metrics = run_backtest(
            bt_df, bs,
            initial_balance=10000,
            risk_percent=risk_config.get('risk_percent', 1.0),
            commission=risk_config.get('commission', 0.001),
            slippage=risk_config.get('slippage', 0.0005),
            stop_buffer=risk_config.get('stop_buffer', 0.002),
            breakeven_at=trailing.get('breakeven_at', 0.3),
            trailing_activate=trailing.get('trail_activate', 0.8),
            trailing_step=trailing.get('trail_step', 0.3),
            max_leverage=risk_config.get('max_leverage', 10),
            dynamic_risk=dynamic_risk,
            cooldown_hours=4.0,
            max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
            max_daily_trades=risk_config.get('max_daily_trades', 20),
            max_consecutive_losses=3,
            funding_rates=funding,
        )

        exit_reasons = {}
        for t in trades:
            r = t.get('exit_reason', '?')
            exit_reasons[r] = exit_reasons.get(r, 0) + 1

        return {
            'signals': len(signals),
            'trades': metrics['total_trades'],
            'pf': metrics['profit_factor'],
            'wr': metrics['win_rate'],
            'ret': metrics['total_return'],
            'dd': metrics['max_drawdown'],
            'exits': exit_reasons,
        }
    finally:
        sf.find_consolidation_center = original_find_sf
        main_mod.find_consolidation_center = original_find_main


if __name__ == '__main__':
    with open('config/settings.yaml', 'r') as f:
        config = yaml.safe_load(f)

    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})
    trailing = config.get('trailing', {})

    exchange = ccxt.bybit({
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'},
    })

    print("=" * 70)
    print("2x2 MATRIX A/B TEST: ETH")
    print("=" * 70)

    print("Fetching ETH data...")
    df = fetch_ohlcv(exchange, 'ETH/USDT:USDT')
    print(f"  {len(df)} candles")

    print("Fetching funding rates...")
    funding = fetch_funding_rates(exchange, 'ETH/USDT:USDT')
    print(f"  {len(funding)} records")

    cells = {}
    combos = [
        ('hl', 'r_multiple', 'H/L + R-multiple (original)'),
        ('hl', 'structure', 'H/L + Structure TP'),
        ('body', 'r_multiple', 'Body + R-multiple'),
        ('body', 'structure', 'Body + Structure TP'),
    ]

    for center, tp, label in combos:
        print(f"\n  Running: {label}...")
        result = run_one_cell(df, funding, config, center, tp, risk_config, dynamic_risk, trailing, 'ETH/USDT:USDT')
        cells[(center, tp)] = result
        if result:
            print(f"    Signals: {result['signals']}, Trades: {result['trades']}, "
                  f"PF: {result['pf']:.2f}, WR: {result['wr']:.1%}, "
                  f"Return: {result['ret']:.1%}, DD: {result['dd']:.1%}")
            print(f"    Exits: {result['exits']}")

    # Print matrix
    print(f"\n{'='*70}")
    print("2x2 MATRIX: PF")
    print(f"{'='*70}")
    print(f"{'':>20} {'r_multiple':>12} {'structure':>12}")
    print("-" * 48)
    for center in ['hl', 'body']:
        row_label = "H/L center" if center == 'hl' else "Body center"
        pf_rm = cells.get((center, 'r_multiple'), {}).get('pf', 0)
        pf_st = cells.get((center, 'structure'), {}).get('pf', 0)
        print(f"{row_label:>20} {pf_rm:>11.2f} {pf_st:>11.2f}")

    print(f"\n{'='*70}")
    print("2x2 MATRIX: Return")
    print(f"{'='*70}")
    print(f"{'':>20} {'r_multiple':>12} {'structure':>12}")
    print("-" * 48)
    for center in ['hl', 'body']:
        row_label = "H/L center" if center == 'hl' else "Body center"
        ret_rm = cells.get((center, 'r_multiple'), {}).get('ret', 0)
        ret_st = cells.get((center, 'structure'), {}).get('ret', 0)
        print(f"{row_label:>20} {ret_rm:>11.1%} {ret_st:>11.1%}")

    print(f"\n{'='*70}")
    print("2x2 MATRIX: Win Rate")
    print(f"{'='*70}")
    print(f"{'':>20} {'r_multiple':>12} {'structure':>12}")
    print("-" * 48)
    for center in ['hl', 'body']:
        row_label = "H/L center" if center == 'hl' else "Body center"
        wr_rm = cells.get((center, 'r_multiple'), {}).get('wr', 0)
        wr_st = cells.get((center, 'structure'), {}).get('wr', 0)
        print(f"{row_label:>20} {wr_rm:>11.1%} {wr_st:>11.1%}")

    print(f"\n{'='*70}")
    print("2x2 MATRIX: Max DD")
    print(f"{'='*70}")
    print(f"{'':>20} {'r_multiple':>12} {'structure':>12}")
    print("-" * 48)
    for center in ['hl', 'body']:
        row_label = "H/L center" if center == 'hl' else "Body center"
        dd_rm = cells.get((center, 'r_multiple'), {}).get('dd', 0)
        dd_st = cells.get((center, 'structure'), {}).get('dd', 0)
        print(f"{row_label:>20} {dd_rm:>11.1%} {dd_st:>11.1%}")

    print(f"\n{'='*70}")
    print("2x2 MATRIX: Trades")
    print(f"{'='*70}")
    print(f"{'':>20} {'r_multiple':>12} {'structure':>12}")
    print("-" * 48)
    for center in ['hl', 'body']:
        row_label = "H/L center" if center == 'hl' else "Body center"
        t_rm = cells.get((center, 'r_multiple'), {}).get('trades', 0)
        t_st = cells.get((center, 'structure'), {}).get('trades', 0)
        print(f"{row_label:>20} {t_rm:>12} {t_st:>12}")

    print(f"\n{'='*70}")
    print("ANALYSIS")
    print(f"{'='*70}")

    pf_hl_rm = cells.get(('hl', 'r_multiple'), {}).get('pf', 0)
    pf_hl_st = cells.get(('hl', 'structure'), {}).get('pf', 0)
    pf_body_rm = cells.get(('body', 'r_multiple'), {}).get('pf', 0)
    pf_body_st = cells.get(('body', 'structure'), {}).get('pf', 0)

    print(f"\nEffect of CENTER CHANGE (row effect):")
    print(f"  r_multiple: H/L={pf_hl_rm:.2f} -> Body={pf_body_rm:.2f} (delta={pf_body_rm-pf_hl_rm:+.2f})")
    print(f"  structure:  H/L={pf_hl_st:.2f} -> Body={pf_body_st:.2f} (delta={pf_body_st-pf_hl_st:+.2f})")

    print(f"\nEffect of TP CHANGE (column effect):")
    print(f"  H/L:     r_multiple={pf_hl_rm:.2f} -> structure={pf_hl_st:.2f} (delta={pf_hl_st-pf_hl_rm:+.2f})")
    print(f"  Body:    r_multiple={pf_body_rm:.2f} -> structure={pf_body_st:.2f} (delta={pf_body_st-pf_body_rm:+.2f})")

    if pf_hl_rm > pf_body_rm:
        print(f"\n>>> H/L center is BETTER than body center (PF {pf_hl_rm:.2f} vs {pf_body_rm:.2f})")
    else:
        print(f"\n>>> Body center is BETTER than H/L center (PF {pf_body_rm:.2f} vs {pf_hl_rm:.2f})")

    if pf_body_st > pf_body_rm:
        print(f">>> Structure TP helps on body center (+{pf_body_st-pf_body_rm:.2f})")
    elif pf_hl_st > pf_hl_rm:
        print(f">>> Structure TP helps on H/L center (+{pf_hl_st-pf_hl_rm:.2f})")
    else:
        print(f">>> Structure TP does NOT help on either center")
