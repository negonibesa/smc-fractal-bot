"""
Full backtest runner — fetches max data from Bybit, runs all 5 pairs.
Usage: python backtest_runner.py
"""
import sys
import time
import yaml
import ccxt
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest import run_backtest, calculate_backtest_metrics, save_backtest_report
from main import SignalGenerator


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None, limit=1000):
    """Fetch OHLCV data from Bybit, paginating to get max available."""
    all_candles = []
    if since is None:
        since = exchange.parse8601('2022-01-01T00:00:00Z')

    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        except Exception as e:
            print(f"  Fetch error: {e}, retrying in 5s...")
            time.sleep(5)
            try:
                candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            except Exception as e2:
                print(f"  Fetch failed again: {e2}, stopping pagination")
                break

        if not candles:
            break

        all_candles.extend(candles)
        last_ts = candles[-1][0]
        since = last_ts + 1

        if len(candles) < limit:
            break

        time.sleep(0.2)

    if not all_candles:
        return pd.DataFrame()

    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df


def ccxt_symbol(bybit_symbol: str) -> str:
    """Convert BNBUSDT -> BNB/USDT:USDT for ccxt Bybit perpetual."""
    base = bybit_symbol.replace('USDT', '')
    return f"{base}/USDT:USDT"


def fetch_funding_rates(exchange, symbol, since=None):
    """Fetch full funding rate history from Bybit, paginating BACKWARDS.

    Bybit's API returns the most recent 200 records and ignores `since`
    for older data. We paginate backwards by passing `endTime` (raw Bybit
    param via ccxt params dict) set to 1ms before the earliest record
    of each page.
    """
    all_rates = []
    end_time = None  # None = most recent (Bybit default)
    page = 0

    while True:
        page += 1
        params = {}
        if end_time is not None:
            params['endTime'] = end_time

        try:
            rates = exchange.fetch_funding_rate_history(symbol, limit=200, params=params)
        except Exception as e:
            print(f"  Funding fetch error: {e}, retrying...")
            time.sleep(3)
            try:
                rates = exchange.fetch_funding_rate_history(symbol, limit=200, params=params)
            except Exception as e2:
                print(f"  Funding fetch failed again: {e2}, stopping")
                break

        if not rates:
            print(f"  Funding page {page}: empty, stopping. Total so far: {len(all_rates)}")
            break

        first_ts = pd.Timestamp(rates[0]['timestamp'], unit='ms')
        last_ts = pd.Timestamp(rates[-1]['timestamp'], unit='ms')
        all_rates.extend(rates)
        print(f"  Funding page {page}: {len(rates)} records, "
              f"{first_ts} to {last_ts}, total: {len(all_rates)}")

        # Move end_time to 1ms before the earliest record in this page
        end_time = rates[0]['timestamp'] - 1

        # If we got fewer than limit, we've reached the beginning
        if len(rates) < 200:
            print(f"  Funding: last page ({len(rates)} < 200), stopping")
            break

        # Safety: stop if we've gone past our `since` boundary
        if since is not None and rates[0]['timestamp'] <= since:
            print(f"  Funding: reached since boundary, stopping")
            break

        time.sleep(0.2)

    if not all_rates:
        return pd.DataFrame(columns=['timestamp', 'rate'])

    df = pd.DataFrame([{
        'timestamp': pd.Timestamp(r['timestamp'], unit='ms'),
        'rate': r['fundingRate']
    } for r in all_rates])
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df


def run_single_backtest(symbol, df, config, risk_config, dynamic_risk, funding_rates=None):
    """Run backtest for a single symbol with its config."""
    sig_config = {
        'strategy': config.get('strategy', {}),
        'filters': config.get('filters', {}),
    }
    gen = SignalGenerator(sig_config, symbol=symbol)

    signals = []
    for i in range(len(df)):
        sig = gen.process_candle(df, i)
        if sig:
            sig['index'] = i
            sig['timestamp'] = str(df['timestamp'].iloc[i])
            signals.append(sig)

    if not signals:
        return None, None, []

    bt_df = df.copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    signals_dict = {s['timestamp']: s for s in signals}
    bt_signals = [signals_dict[ts] for ts in bt_df.index if ts in signals_dict]

    if not bt_signals:
        return None, None, []

    trailing = config.get('trailing', {})

    trades, metrics = run_backtest(
        bt_df, bt_signals,
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
        cooldown_hours=4.0,
        max_daily_loss_pct=risk_config.get('max_daily_loss', 5.0),
        max_daily_trades=risk_config.get('max_daily_trades', 20),
        funding_rates=funding_rates,
    )

    return metrics, trades


def yearly_breakdown(trades, df):
    """Calculate metrics per year."""
    if not trades or 'entry_time' not in trades[0]:
        return {}

    df_by_ts = df.set_index('timestamp')
    yearly = {}

    for trade in trades:
        et = trade['entry_time']
        if isinstance(et, str):
            try:
                et = pd.Timestamp(et)
            except:
                continue
        year = et.year if hasattr(et, 'year') else 'unknown'
        if year not in yearly:
            yearly[year] = {'trades': 0, 'wins': 0, 'total_pnl': 0}
        yearly[year]['trades'] += 1
        yearly[year]['total_pnl'] += trade['pnl']
        if trade['pnl'] > 0:
            yearly[year]['wins'] += 1

    for year, data in yearly.items():
        data['win_rate'] = data['wins'] / data['trades'] if data['trades'] > 0 else 0
        # PF for the year
        year_trades = [t for t in trades if
                       str(t['entry_time'])[:4] == str(year)]
        wins_pnl = sum(t['pnl'] for t in year_trades if t['pnl'] > 0)
        loss_pnl = abs(sum(t['pnl'] for t in year_trades if t['pnl'] <= 0))
        data['pf'] = wins_pnl / loss_pnl if loss_pnl > 0 else (
            float('inf') if wins_pnl > 0 else 0)

    return yearly


def main():
    # Load config
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})

    # Init Bybit (public API for data)
    exchange = ccxt.bybit({'enableRateLimit': True})

    symbols = config.get('assets', [])
    all_results = {}

    for asset in symbols:
        symbol = asset['symbol']
        asset_config = asset.get('config', config)

        # Merge with global defaults
        full_config = {
            'strategy': {**config.get('strategy', {}), **asset_config.get('strategy', {})},
            'filters': {**config.get('filters', {}), **asset_config.get('filters', {})},
            'trailing': {**config.get('trailing', {}), **asset_config.get('trailing', {})},
        }

        print(f"\n{'='*60}")
        print(f"  {symbol}")
        print(f"{'='*60}")

        print(f"  Fetching max 4H data from Bybit...")
        df = fetch_ohlcv(exchange, ccxt_symbol(symbol), timeframe='4h')

        if df.empty:
            print(f"  No data for {symbol}, skipping")
            continue

        years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
        print(f"  Got {len(df)} candles, {years:.1f} years "
              f"({df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]})")

        print(f"  Fetching funding rate history...")
        funding_rates = fetch_funding_rates(exchange, ccxt_symbol(symbol))
        print(f"  Got {len(funding_rates)} funding rate records")

        print(f"  Running backtest...")
        metrics, trades = run_single_backtest(
            symbol, df, full_config, risk_config, dynamic_risk,
            funding_rates=funding_rates if not funding_rates.empty else None
        )

        if metrics is None:
            print(f"  No trades generated for {symbol}")
            continue

        yearly = yearly_breakdown(trades, df)

        print(f"\n  RESULTS:")
        print(f"  Trades: {metrics['total_trades']}")
        print(f"  Win Rate: {metrics['win_rate']:.1%}")
        print(f"  Profit Factor: {metrics['profit_factor']:.2f}")
        print(f"  Max Drawdown: {metrics['max_drawdown']:.1%}")
        print(f"  Total Return: {metrics['total_return']:.1%}")
        print(f"  Sharpe: {metrics['sharpe_ratio']:.2f}")
        print(f"  Total Funding: ${metrics.get('total_funding', 0):.2f}")

        if yearly:
            print(f"\n  YEARLY BREAKDOWN:")
            for year in sorted(yearly.keys()):
                y = yearly[year]
                print(f"    {year}: {y['trades']} trades, "
                      f"WR={y['win_rate']:.0%}, PF={y['pf']:.2f}, "
                      f"PnL=${y['total_pnl']:.0f}")

        all_results[symbol] = {
            'df_len': len(df),
            'years': years,
            'metrics': metrics,
            'yearly': yearly,
            'trades': trades,
        }

        # Save report
        report_path = Path(__file__).parent / "backtest_results" / f"{symbol}.txt"
        report_path.parent.mkdir(exist_ok=True)
        save_backtest_report(metrics, trades, str(report_path))

    # Summary
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    for symbol, data in all_results.items():
        m = data['metrics']
        print(f"  {symbol:12s}: PF={m['profit_factor']:6.2f}  "
              f"WR={m['win_rate']:.0%}  "
              f"Trades={m['total_trades']:3d}  "
              f"Ret={m['total_return']:+.1%}  "
              f"DD={m['max_drawdown']:.1%}  "
              f"Funding=${m.get('total_funding', 0):.0f}  "
              f"({data['years']:.1f}y)")

    # Save summary
    summary_path = Path(__file__).parent / "backtest_results" / "summary.txt"
    summary_path.parent.mkdir(exist_ok=True)
    with open(summary_path, 'w') as f:
        f.write(f"BACKTEST SUMMARY — {datetime.now()}\n")
        f.write(f"{'='*60}\n")
        f.write(f"Config: commission={risk_config.get('commission', 0.001)}, "
                f"slippage={risk_config.get('slippage', 0.0005)}, "
                f"max_leverage={risk_config.get('max_leverage', 10)}, "
                f"cooldown=4H, dynamic_risk={'ON' if dynamic_risk.get('enabled') else 'OFF'}\n\n")
        for symbol, data in all_results.items():
            m = data['metrics']
            f.write(f"{symbol:12s}: PF={m['profit_factor']:6.2f}  "
                    f"WR={m['win_rate']:.0%}  "
                    f"Trades={m['total_trades']:3d}  "
                    f"Ret={m['total_return']:+.1%}  "
                    f"DD={m['max_drawdown']:.1%}  "
                    f"({data['years']:.1f}y)\n")
            if data['yearly']:
                for year in sorted(data['yearly'].keys()):
                    y = data['yearly'][year]
                    f.write(f"  {year}: {y['trades']}t WR={y['win_rate']:.0%} "
                            f"PF={y['pf']:.2f} PnL=${y['total_pnl']:.0f}\n")
            f.write("\n")

    print(f"\n  Reports saved to backtest_results/")
    print(f"  Summary: {summary_path}")


if __name__ == '__main__':
    main()
