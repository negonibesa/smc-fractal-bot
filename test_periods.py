"""
Period stress test — Grok's specific historical periods.
Downloads 4H data from Bybit and runs strategy on each period.
"""
import warnings; warnings.filterwarnings('ignore')
import yaml, time, numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime
from backtest import run_backtest
from smc_features import find_consolidation_center, detect_sweep, calculate_adx
from dotenv import load_dotenv
import os

load_dotenv()

PERIODS = {
    "Bear_2022":     ("2022-06-01", "2022-11-30", "Bear: BTC 30K->16K"),
    "Recovery_2023": ("2023-01-01", "2023-03-31", "Bull recovery +50%"),
    "Sideways_2024": ("2024-04-01", "2024-09-30", "Sideways chop"),
    "Covid_Crash":   ("2020-03-01", "2020-05-31", "COVID extreme vol"),
    "Bull_Peak":     ("2021-10-01", "2021-11-30", "BTC 69K euphoria"),
    "Correction_2021":("2021-05-01","2021-07-31","50% correction"),
}

def download_data(symbol, start_date, end_date):
    from core.bybit_client import BybitClient
    api_key = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    client = BybitClient(api_key, api_secret, testnet=True)

    start_ms = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
    end_ms = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)

    all_data = []
    current_start = start_ms
    retries = 0

    while current_start < end_ms:
        try:
            r = client.get_klines(symbol, "240", limit=1000, start=current_start, end=end_ms)
            rows = r.get("list", [])
            if not rows:
                break
            for row in rows:
                all_data.append({
                    "timestamp": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                })
            last_ts = int(rows[-1][0])
            if last_ts <= current_start:
                break
            current_start = last_ts + 1
            time.sleep(0.15)
            retries = 0
        except Exception as e:
            retries += 1
            if retries > 3:
                break
            time.sleep(1)

    if not all_data:
        return None
    df = pd.DataFrame(all_data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    return df


def ensure_data(symbol, start_date, end_date):
    cache = f"data/raw/periods/{symbol}_{start_date}_{end_date}.csv"
    Path(cache).parent.mkdir(parents=True, exist_ok=True)
    if Path(cache).exists():
        df = pd.read_csv(cache)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        for c in ["open","high","low","close","volume"]:
            df[c] = pd.to_numeric(df[c])
        if len(df) > 10:
            return df

    print(f"    Downloading {symbol} {start_date}...")
    df = download_data(symbol, start_date, end_date)
    if df is not None and len(df) > 0:
        df.to_csv(cache, index=False)
        print(f"    Saved {len(df)} candles")
    return df


def run_bt(df_part, params, trailing):
    lookback = params["lookback"]
    if len(df_part) < lookback + 10:
        return None

    center = find_consolidation_center(df_part, lookback=lookback)
    sweep = detect_sweep(df_part, center, threshold=params["sweep_threshold"])
    adx, pdi, mdi = calculate_adx(df_part, period=14)

    signals = []
    state = 0; sw_dir = None; sw_price = None; sw_idx = None

    for i in range(lookback, len(df_part)):
        c = center.iloc[i] if not pd.isna(center.iloc[i]) else df_part["close"].iloc[i]
        h, l, cl = df_part["high"].iloc[i], df_part["low"].iloc[i], df_part["close"].iloc[i]

        if state == 0:
            if sweep["bearish_sweep"].iloc[i]:
                state=1; sw_dir="bearish"; sw_price=h; sw_idx=i
            elif sweep["bullish_sweep"].iloc[i]:
                state=1; sw_dir="bullish"; sw_price=l; sw_idx=i
        elif state == 1:
            if abs(cl - c) / c < params["center_proximity"]:
                cur_adx = adx.iloc[i] if not pd.isna(adx.iloc[i]) else 0
                if cur_adx < 25:
                    state=0; sw_dir=None; continue
                if cur_adx > 25:
                    pv = pdi.iloc[i] if not pd.isna(pdi.iloc[i]) else 0
                    mv = mdi.iloc[i] if not pd.isna(mdi.iloc[i]) else 0
                    if sw_dir=="bearish" and mv<pv: state=0; sw_dir=None; continue
                    if sw_dir=="bullish" and pv<mv: state=0; sw_dir=None; continue

                entry = c
                if sw_dir=="bearish":
                    stop=sw_price*1.003; risk=abs(entry-stop)
                    tp=entry-risk*params["tp_multiplier"]; d="SELL"
                else:
                    stop=sw_price*0.997; risk=abs(stop-entry)
                    tp=entry+risk*params["tp_multiplier"]; d="BUY"
                signals.append({"index":i,"timestamp":str(df_part["timestamp"].iloc[i]),
                                "direction":d,"entry":entry,"stop":stop,"tp":tp,"confidence":0.7})
                state=0; sw_dir=None
            elif i-sw_idx > params["timeout"]:
                state=0; sw_dir=None

    if len(signals) < 2:
        return None

    bt_df = df_part.iloc[lookback:].copy()
    bt_df.index = [str(t) for t in bt_df["timestamp"]]
    sd = {s["timestamp"]: s for s in signals}
    bs = [sd[ts] for ts in bt_df.index if ts in sd]
    if not bs:
        return None

    _, m = run_backtest(bt_df, bs, initial_balance=10000, risk_percent=1.0,
        breakeven_at=trailing.get("breakeven_at",0.5),
        trailing_activate=trailing.get("trail_activate",1.0),
        trailing_step=trailing.get("trail_step",0.5))
    return {"pf":m["profit_factor"],"wr":m["win_rate"],
            "ret":m["total_return"],"dd":m["max_drawdown"],"trades":m["total_trades"]}


if __name__ == "__main__":
    with open("config/settings.yaml") as f:
        config = yaml.safe_load(f)

    def get_pair_config(symbol):
        for asset in config.get("assets", []):
            if asset["symbol"] == symbol and "config" in asset:
                return asset["config"]
        return {"strategy": config["strategy"], "filters": config.get("filters", {}),
                "trailing": config.get("trailing", {})}

    symbols = ["BTCUSDT", "XRPUSDT", "FILUSDT"]

    print("=" * 80)
    print("  PERIOD STRESS TEST — Grok's Historical Market Regimes")
    print("=" * 80)

    all_results = {}

    for symbol in symbols:
        print(f"\n{'#' * 80}")
        print(f"  {symbol}")
        print(f"{'#' * 80}")

        pcfg = get_pair_config(symbol)
        params = pcfg["strategy"]
        trail = pcfg.get("trailing", {})

        pair_results = {}

        for pname, (start, end, desc) in PERIODS.items():
            print(f"\n  {pname} ({start} -> {end}) {desc}")
            df = ensure_data(symbol, start, end)
            if df is None or len(df) < 50:
                print(f"    NO DATA")
                continue
            print(f"    {len(df)} candles")

            result = run_bt(df, params, trail)
            if result:
                pair_results[pname] = result
                e = "GREEN" if result["pf"]>=1.5 else "YELLOW" if result["pf"]>=1.0 else "RED"
                print(f"    [{e}] PF={result['pf']:>6.2f} WR={result['wr']:>5.0%} "
                      f"Ret={result['ret']:>+7.2%} DD={result['dd']:>5.2%} T={result['trades']:>3}")
            else:
                print(f"    N/A")
        all_results[symbol] = pair_results

    # ─── SUMMARY TABLE ────────────────────────────────────────
    print(f"\n\n{'=' * 80}")
    print(f"  SUMMARY")
    print(f"{'=' * 80}")

    header = f"{'Symbol':<14}"
    for p in PERIODS:
        header += f" {p:<16}"
    header += " AVG_PF"
    print(header)
    print("-" * 80)

    for symbol in symbols:
        row = f"{symbol:<14}"
        pfs = []
        for pname in PERIODS:
            r = all_results.get(symbol, {}).get(pname)
            if r:
                pfs.append(r["pf"])
                e = "+" if r["pf"]>=1.5 else "~" if r["pf"]>=1.0 else "-"
                row += f" {e}{r['pf']:>5.2f}            "
            else:
                row += f" {'N/A':>10}      "
        avg = np.mean(pfs) if pfs else 0
        row += f" {avg:>5.2f}"
        print(row)

    print(f"\n[+] PF>=1.5  [~] PF>=1.0  [-] PF<1.0")

    # ─── VERDICTS ─────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print(f"  VERDICTS")
    print(f"{'=' * 80}")

    for symbol in symbols:
        res = all_results.get(symbol, {})
        if not res:
            print(f"  {symbol}: NO DATA")
            continue
        pfs = [r["pf"] for r in res.values()]
        avg = np.mean(pfs)
        profitable = sum(1 for p in pfs if p >= 1.0)
        strong = sum(1 for p in pfs if p >= 1.5)
        total = len(pfs)
        verdict = "ROBUST" if avg >= 1.5 and profitable >= total-1 else \
                  "GOOD" if avg >= 1.2 and profitable >= total-1 else \
                  "RISKY" if avg >= 1.0 else "REJECT"
        print(f"  {symbol}: avg PF={avg:.2f}  "
              f"profitable={profitable}/{total}  strong={strong}/{total}  [{verdict}]")
