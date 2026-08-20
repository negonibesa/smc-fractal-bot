"""
Bulk backtest scanner — tests 15 top coins with default strategy params.
No optimization bias: all pairs use identical baseline config.
Usage: python bulk_backtest.py
"""
import sys
import time
import yaml
import ccxt
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).parent))

from backtest import run_backtest, calculate_backtest_metrics
from main import SignalGenerator


# ─── 15 coins from Bybit top 30 by market cap ────────────────
SCAN_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "DOTUSDT", "TRXUSDT",
    "LINKUSDT", "LTCUSDT", "NEARUSDT", "SUIUSDT", "ARBUSDT",
]


def ccxt_symbol(bybit_symbol: str) -> str:
    base = bybit_symbol.replace('USDT', '')
    return f"{base}/USDT:USDT"


def fetch_ohlcv(exchange, symbol, timeframe='4h', since=None, limit=1000):
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
                print(f"  Fetch failed again: {e2}, stopping")
                break

        if not candles:
            break

        all_candles.extend(candles)
        last_ts = candles[-1][0]
        since = last_ts + 1

        if len(candles) < limit:
            break

        time.sleep(0.15)

    if not all_candles:
        return pd.DataFrame()

    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df


def fetch_funding_rates(exchange, symbol, since=None):
    all_rates = []
    end_time = None
    page = 0

    while True:
        page += 1
        params = {}
        if end_time is not None:
            params['endTime'] = end_time

        try:
            rates = exchange.fetch_funding_rate_history(symbol, limit=200, params=params)
        except Exception as e:
            print(f"  Funding error: {e}, retrying...")
            time.sleep(3)
            try:
                rates = exchange.fetch_funding_rate_history(symbol, limit=200, params=params)
            except:
                break

        if not rates:
            break

        all_rates.extend(rates)
        print(f"    Funding page {page}: {len(rates)} records, total {len(all_rates)}")

        end_time = rates[0]['timestamp'] - 1

        if len(rates) < 200:
            break
        if since is not None and rates[0]['timestamp'] <= since:
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


def run_single_backtest(symbol, df, config, risk_config, dynamic_risk, funding_rates=None):
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
        return None, []

    bt_df = df.copy()
    bt_df.index = [str(t) for t in bt_df['timestamp']]
    signals_dict = {s['timestamp']: s for s in signals}
    bt_signals = [signals_dict[ts] for ts in bt_df.index if ts in signals_dict]

    if not bt_signals:
        return None, []

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


def yearly_breakdown(trades):
    if not trades or 'entry_time' not in trades[0]:
        return {}

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
        year_trades = [t for t in trades if str(t['entry_time'])[:4] == str(year)]
        wins_pnl = sum(t['pnl'] for t in year_trades if t['pnl'] > 0)
        loss_pnl = abs(sum(t['pnl'] for t in year_trades if t['pnl'] <= 0))
        data['pf'] = wins_pnl / loss_pnl if loss_pnl > 0 else (float('inf') if wins_pnl > 0 else 0)

    return yearly


def main():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    risk_config = config.get('risk', {})
    dynamic_risk = config.get('dynamic_risk', {})

    # Default strategy params — same for ALL pairs (no optimization bias)
    default_config = {
        'strategy': {
            'lookback': 12,
            'sweep_threshold': 0.008,
            'center_proximity': 0.012,
            'tp_multiplier': 1.0,
            'timeout': 15,
        },
        'filters': {
            'adx_filter': {'enabled': True, 'min_adx': 25},
        },
        'trailing': {
            'enabled': True,
            'breakeven_at': 0.5,
            'trail_activate': 1.0,
            'trail_step': 0.5,
        },
    }

    exchange = ccxt.bybit({'enableRateLimit': True})

    all_results = []
    start_time = time.time()

    print(f"\n{'='*70}")
    print(f"  BULK BACKTEST — {len(SCAN_SYMBOLS)} coins, default config")
    print(f"  Commission: {risk_config.get('commission', 0.001):.1%}, "
          f"Slippage: {risk_config.get('slippage', 0.0005):.2%}, "
          f"MaxLeverage: {risk_config.get('max_leverage', 10)}x, "
          f"DynamicRisk: ON")
    print(f"{'='*70}\n")

    for idx, symbol in enumerate(SCAN_SYMBOLS):
        cs = ccxt_symbol(symbol)
        print(f"[{idx+1}/{len(SCAN_SYMBOLS)}] {symbol}")

        # Fetch OHLCV
        print(f"  Fetching 4H data...")
        df = fetch_ohlcv(exchange, cs)

        if df.empty:
            print(f"  No data, skipping")
            all_results.append({'symbol': symbol, 'error': 'no_data'})
            continue

        years = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days / 365.25
        print(f"  {len(df)} candles, {years:.1f}Y ({df['timestamp'].iloc[0].date()} to {df['timestamp'].iloc[-1].date()})")

        # Fetch funding
        print(f"  Fetching funding rates...")
        funding_rates = fetch_funding_rates(exchange, cs)
        print(f"  {len(funding_rates)} funding records")

        # Backtest
        print(f"  Running backtest...")
        metrics, trades = run_single_backtest(
            symbol, df, default_config, risk_config, dynamic_risk,
            funding_rates=funding_rates if not funding_rates.empty else None
        )

        if metrics is None or metrics['total_trades'] == 0:
            print(f"  No trades generated")
            all_results.append({'symbol': symbol, 'error': 'no_trades'})
            continue

        yearly = yearly_breakdown(trades)

        # Calc per-year stats
        min_yearly_pf = min(y['pf'] for y in yearly.values()) if yearly else 0
        max_yearly_dd = 0  # approximate from trades
        total_pnl = sum(t['pnl'] for t in trades)
        avg_trade = total_pnl / len(trades) if trades else 0

        # Exit reason breakdown
        exit_reasons = {}
        for t in trades:
            r = t.get('exit_reason', 'unknown')
            exit_reasons[r] = exit_reasons.get(r, 0) + 1

        result = {
            'symbol': symbol,
            'candles': len(df),
            'years': years,
            'trades': metrics['total_trades'],
            'win_rate': metrics['win_rate'],
            'pf': metrics['profit_factor'],
            'max_dd': metrics['max_drawdown'],
            'total_return': metrics['total_return'],
            'sharpe': metrics['sharpe_ratio'],
            'final_balance': metrics['final_balance'],
            'total_funding': metrics.get('total_funding', 0),
            'yearly': yearly,
            'min_yearly_pf': min_yearly_pf,
            'avg_trade': avg_trade,
            'exit_reasons': exit_reasons,
        }
        all_results.append(result)

        print(f"  PF={metrics['profit_factor']:.2f}  WR={metrics['win_rate']:.0%}  "
              f"Trades={metrics['total_trades']}  Ret={metrics['total_return']:+.1%}  "
              f"DD={metrics['max_drawdown']:.1%}  "
              f"Funding=${metrics.get('total_funding', 0):.0f}")
        print()

        time.sleep(0.5)

    elapsed = time.time() - start_time

    # ─── Summary Report ──────────────────────────────────────
    successful = [r for r in all_results if 'error' not in r]
    failed = [r for r in all_results if 'error' in r]

    print(f"\n{'='*70}")
    print(f"  SUMMARY — {len(successful)}/{len(SCAN_SYMBOLS)} coins backtested")
    print(f"  Time: {elapsed/60:.1f} min")
    print(f"{'='*70}\n")

    # Sort by PF descending
    successful.sort(key=lambda x: x['pf'], reverse=True)

    table_data = []
    for r in successful:
        table_data.append([
            r['symbol'],
            f"{r['pf']:.2f}",
            f"{r['win_rate']:.0%}",
            r['trades'],
            f"{r['total_return']:+.1%}",
            f"{r['max_dd']:.1%}",
            f"{r['sharpe']:.2f}",
            f"{r['min_yearly_pf']:.2f}",
            f"${r['total_funding']:.0f}",
            f"{r['years']:.1f}Y",
        ])

    headers = ['Symbol', 'PF', 'WinRate', 'Trades', 'Return', 'MaxDD', 'Sharpe', 'MinYrPF', 'Funding', 'Data']
    print(tabulate(table_data, headers=headers, tablefmt='simple'))

    # ─── Ranked Report (markdown) ────────────────────────────
    report_lines = []
    report_lines.append(f"# Bulk Backtest Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report_lines.append(f"\n**Config:** lookback=12, sweep=0.008, center=0.012, tp=1.0, timeout=15, ADX=25, commission=0.1%, slippage=0.05%, max_lev=10x, dynamic_risk=ON")
    report_lines.append(f"\n**Data:** max available 4H Bybit perpetual + funding rates")
    report_lines.append(f"\n## Rankings (by Profit Factor)\n")
    report_lines.append("| Rank | Symbol | PF | WinRate | Trades | Return | MaxDD | Sharpe | MinYrPF | Funding | Data |")
    report_lines.append("|------|--------|----|---------|--------|--------|-------|--------|---------|---------|------|")

    for i, r in enumerate(successful):
        pf_marker = " ✅" if r['pf'] >= 1.3 else " ❌"
        report_lines.append(
            f"| {i+1} | {r['symbol']}{pf_marker} | {r['pf']:.2f} | {r['win_rate']:.0%} | "
            f"{r['trades']} | {r['total_return']:+.1%} | {r['max_dd']:.1%} | {r['sharpe']:.2f} | "
            f"{r['min_yearly_pf']:.2f} | ${r['total_funding']:.0f} | {r['years']:.1f}Y |"
        )

    report_lines.append(f"\n## PF > 1.3 Candidates\n")
    candidates = [r for r in successful if r['pf'] >= 1.3]
    if candidates:
        for r in candidates:
            report_lines.append(f"### {r['symbol']} — PF {r['pf']:.2f}")
            report_lines.append(f"- Win Rate: {r['win_rate']:.0%}, Trades: {r['trades']}, Return: {r['total_return']:+.1%}")
            report_lines.append(f"- MaxDD: {r['max_dd']:.1%}, Sharpe: {r['sharpe']:.2f}, MinYrPF: {r['min_yearly_pf']:.2f}")
            report_lines.append(f"- Data: {r['years']:.1f}Y ({r['candles']} candles), Funding: ${r['total_funding']:.0f}")
            report_lines.append(f"- Exit reasons: {r['exit_reasons']}")
            if r['yearly']:
                report_lines.append(f"- Yearly breakdown:")
                for year in sorted(r['yearly'].keys()):
                    y = r['yearly'][year]
                    report_lines.append(f"  - {year}: {y['trades']}t WR={y['win_rate']:.0%} PF={y['pf']:.2f} PnL=${y['total_pnl']:.0f}")
            report_lines.append("")
    else:
        report_lines.append("No coins with PF > 1.3 found.\n")

    if failed:
        report_lines.append(f"## Failed ({len(failed)})\n")
        for r in failed:
            report_lines.append(f"- {r['symbol']}: {r['error']}")
        report_lines.append("")

    report_lines.append(f"\n---\n*Elapsed: {elapsed/60:.1f} min*\n")

    # Save report
    results_dir = Path(__file__).parent / "bulk_results"
    results_dir.mkdir(exist_ok=True)

    report_path = results_dir / "report.md"
    with open(report_path, 'w') as f:
        f.write('\n'.join(report_lines))

    # Save raw CSV
    csv_data = []
    for r in successful:
        csv_data.append({
            'symbol': r['symbol'], 'pf': r['pf'], 'win_rate': r['win_rate'],
            'trades': r['trades'], 'return': r['total_return'], 'max_dd': r['max_dd'],
            'sharpe': r['sharpe'], 'min_yr_pf': r['min_yearly_pf'],
            'funding': r['total_funding'], 'years': r['years'], 'candles': r['candles'],
            'final_balance': r['final_balance'], 'avg_trade': r['avg_trade'],
        })
    pd.DataFrame(csv_data).to_csv(results_dir / "results.csv", index=False)

    print(f"\nReport saved: {report_path}")
    print(f"CSV saved:    {results_dir / 'results.csv'}")
    print(f"\nCandidates with PF >= 1.3: {[r['symbol'] for r in candidates]}")


if __name__ == '__main__':
    main()
