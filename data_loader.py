import os
import time
import logging
import requests
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_URL = "https://api.bybit.com"
DATA_DIR = Path(__file__).parent / "data" / "raw"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class BybitLoader:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def get_klines(self, symbol: str, interval: str = "60", limit: int = 200) -> pd.DataFrame:
        url = f"{BASE_URL}/v5/market/kline"
        params = {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit}
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if data.get("retCode") != 0:
                logger.error(f"Bybit API error: {data.get('retMsg')}")
                return pd.DataFrame()
            rows = data["result"]["list"]
            df = pd.DataFrame(rows, columns=[
                "timestamp", "open", "high", "low", "close", "volume", "turnover"
            ])
            for col in ["open", "high", "low", "close", "volume", "turnover"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["timestamp"] = pd.to_datetime(pd.to_numeric(df["timestamp"]), unit="ms")
            df = df.sort_values("timestamp").reset_index(drop=True)
            logger.info(f"Loaded {len(df)} candles for {symbol} ({interval}m)")
            return df
        except Exception as e:
            logger.error(f"Error loading klines: {e}")
            return pd.DataFrame()

    def get_multi_tf(self, symbol: str) -> dict:
        timeframes = {"1h": "60", "4h": "240", "1d": "D"}
        data = {}
        for tf_name, tf_code in timeframes.items():
            df = self.get_klines(symbol, interval=tf_code, limit=500)
            if not df.empty:
                data[tf_name] = df
            time.sleep(0.5)
        return data

    def save_to_csv(self, df: pd.DataFrame, filename: str) -> Path:
        filepath = DATA_DIR / filename
        df.to_csv(filepath, index=False)
        logger.info(f"Saved to {filepath}")
        return filepath

    def load_from_csv(self, filename: str) -> pd.DataFrame:
        filepath = DATA_DIR / filename
        if filepath.exists():
            df = pd.read_csv(filepath)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            logger.info(f"Loaded {len(df)} rows from {filepath}")
            return df
        logger.warning(f"File not found: {filepath}")
        return pd.DataFrame()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bybit Data Loader")
    parser.add_argument("--test", action="store_true", help="Test data loading")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading pair")
    args = parser.parse_args()

    if args.test:
        loader = BybitLoader()
        print(f"Testing data loading for {args.symbol}...")
        df = loader.get_klines(args.symbol, interval="60", limit=100)
        if not df.empty:
            print(f"Success! Loaded {len(df)} candles")
            print(df.head())
            loader.save_to_csv(df, f"{args.symbol}_1h_test.csv")
        else:
            print("Failed to load data")
