"""Download 1D data."""
import requests, pandas as pd, time
from pathlib import Path

data_dir = Path('data/raw')
for symbol in ['BNBUSDT', 'XRPUSDT', 'SOLUSDT', 'LINKUSDT']:
    all_1d = []
    end_time = None
    for page in range(4):
        params = {'category':'linear','symbol':symbol,'interval':'D','limit':1000}
        if end_time:
            params['end'] = end_time
        r = requests.get('https://api-testnet.bybit.com/v5/market/kline', params=params)
        data = r.json()
        klines = data['result'].get('list', [])
        if not klines:
            break
        all_1d.extend(klines)
        end_time = int(klines[-1][0]) - 1
        time.sleep(0.3)
    
    if all_1d:
        df = pd.DataFrame(all_1d, columns=['timestamp','open','high','low','close','volume','turnover'])
        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
        for c in ['open','high','low','close','volume','turnover']:
            df[c] = pd.to_numeric(df[c])
        df = df.sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
        df.to_csv(data_dir / f'{symbol}_1d_4y.csv', index=False)
        print(f'{symbol} 1D: {len(df)} candles, {df["timestamp"].min()} to {df["timestamp"].max()}')
