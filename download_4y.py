"""Download 4-year data for BNB, XRP, SOL, LINK."""
import warnings; warnings.filterwarnings('ignore')
import sys; sys.path.insert(0, '.')
import pandas as pd
from dotenv import load_dotenv
import os
from core.bybit_client import BybitClient
from pathlib import Path
import time

load_dotenv()
# Use mainnet for historical data (testnet doesn't have 4 years)
bc = BybitClient(os.getenv('BYBIT_API_KEY'), os.getenv('BYBIT_API_SECRET'), testnet=False)
bc.BASE_URL = "https://api.bybit.com"  # force mainnet for data download
data_dir = Path('data/raw')
data_dir.mkdir(parents=True, exist_ok=True)

for symbol in ['BNBUSDT', 'XRPUSDT', 'SOLUSDT', 'LINKUSDT']:
    print(f'Downloading {symbol}...')
    
    # 4H candles
    all_rows = []
    end_time = None
    for page in range(8):
        params = {"category": "linear", "symbol": symbol, "interval": "240", "limit": 1000}
        if end_time:
            params["end"] = end_time
        try:
            result = bc._request("GET", "/v5/market/kline", params)
            klines = result.get('list', [])
        except Exception as e:
            print(f'  Error: {e}')
            break
        if not klines:
            print(f'  Page {page+1}: no data')
            break
        for k in klines:
            all_rows.append(k)
        end_time = int(klines[-1][0]) - 1
        ts = pd.to_datetime(int(klines[0][0]), unit='ms')
        print(f'  Page {page+1}: {len(klines)} candles, from {ts}')
        time.sleep(0.5)
    
    if all_rows:
        df = pd.DataFrame(all_rows, columns=['timestamp','open','high','low','close','volume','turnover'])
        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
        for c in ['open','high','low','close','volume','turnover']:
            df[c] = pd.to_numeric(df[c])
        df = df.sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
        path = data_dir / f'{symbol}_4h_4y.csv'
        df.to_csv(path, index=False)
        print(f'  4H saved: {len(df)} candles, {df["timestamp"].min()} to {df["timestamp"].max()}')
    else:
        print(f'  No 4H data downloaded')
    
    # 1D candles
    all_1d = []
    end_time = None
    for page in range(4):
        params = {"category": "linear", "symbol": symbol, "interval": "1D", "limit": 1000}
        if end_time:
            params["end"] = end_time
        try:
            result = bc._request("GET", "/v5/market/kline", params)
            klines = result.get('list', [])
        except Exception as e:
            print(f'  1D Error: {e}')
            break
        if not klines:
            break
        for k in klines:
            all_1d.append(k)
        end_time = int(klines[-1][0]) - 1
        time.sleep(0.5)
    
    if all_1d:
        df_1d = pd.DataFrame(all_1d, columns=['timestamp','open','high','low','close','volume','turnover'])
        df_1d['timestamp'] = pd.to_datetime(df_1d['timestamp'].astype(int), unit='ms')
        for c in ['open','high','low','close','volume','turnover']:
            df_1d[c] = pd.to_numeric(df_1d[c])
        df_1d = df_1d.sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
        path_1d = data_dir / f'{symbol}_1d_4y.csv'
        df_1d.to_csv(path_1d, index=False)
        print(f'  1D saved: {len(df_1d)} candles')
