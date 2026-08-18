# SMC Fractal Bot

Autonomous trading bot based on Smart Money Concepts (SMC).

## Strategy

**Sweep → Return to Center → Enter OPPOSITE**

1. Detect liquidity sweep on 4H timeframe
2. Wait for price to return to consolidation center
3. Enter OPPOSITE to sweep direction
4. "Before impulse, price always returns to equilibrium"

## Files

```
smc_fractal_bot/
├── config/
│   ├── settings.yaml      # All parameters (on/off toggles)
│   └── best.yaml          # Best config from auto-optimizer
├── auto_optimize.py       # Auto-optimizer (tests all combos)
├── backtest.py            # Backtest engine with trailing stop
├── smc_features.py        # SMC detection (sweep, center, ADX)
├── data_loader.py         # Bybit API data loader
├── visualize_trades.py    # Trade visualization charts
├── Dia.md                 # Project status
├── data/raw/              # Price data (BTC/SOL/BNB)
└── logs/                  # Charts & reports
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run auto-optimizer
python auto_optimize.py

# Visualize trades
python visualize_trades.py
```

## Configuration

Edit `config/settings.yaml`:
- Toggle filters ON/OFF
- Set parameter ranges
- Configure risk management

## Results (12M BNB)

| Metric | Value |
|--------|-------|
| Profit Factor | 4.53 |
| Return | +27.09% |
| Win Rate | 70% |
| Trades | 46 |
| Max Drawdown | 1.85% |

## Risk Management

- 1% risk per trade
- Trailing stop (BE at 0.5x, trail at 1.0x)
- Commission: 0.1%
- Slippage: 0.05%
