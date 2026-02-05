# dumbtrader

A futures trading bot built on the [ProjectX](https://www.projectx.com/) API. Define strategies with YAML or a subset of Pine Script v5 and run them against paper or live accounts.

## Setup

Requires Python 3.11+.

```bash
pip install -e .
```

Copy `.env.example` to `.env` and fill in your ProjectX / TopStepX credentials:

```
PROJECT_X_USERNAME=your_username
PROJECT_X_API_KEY=your_api_key
```

## Configuration

Edit `config.yaml` to set your trading parameters:

```yaml
api:
  username: ${PROJECT_X_USERNAME}
  api_key: ${PROJECT_X_API_KEY}

trading:
  mode: paper          # "paper" or "live"
  instrument: MNQ
  timeframe: 1min
  max_position_size: 2

strategy:
  file: strategies/example_sma_cross.yaml   # or .pine file
```

Environment variables are interpolated with `${VAR}` syntax.

## Usage

```bash
# Run with default config.yaml (paper mode)
dumbtrader

# Specify a config file
dumbtrader -c my_config.yaml

# Dry-run mode (evaluates strategy but doesn't place orders)
dumbtrader --dry-run

# Live trading (requires mode: live in config + confirmation prompt)
dumbtrader --live
```

## Strategies

### YAML

Define indicators and rules declaratively. Supports `crossover`, `crossunder`, and comparison operators.

```yaml
name: SMA Crossover
inputs:
  fast_period: 9
  slow_period: 21
  size: 1

indicators:
  fast_sma: { type: sma, period: $fast_period }
  slow_sma: { type: sma, period: $slow_period }

rules:
  entry_long:
    condition: crossover(fast_sma, slow_sma)
    action: buy
    size: $size
  exit_long:
    condition: crossunder(fast_sma, slow_sma)
    action: close_long
```

Available indicators: `sma`, `ema`, `rsi`, `macd`, `bbands`, `atr`.

### Pine Script

Write strategies using a subset of Pine Script v5:

```pine
//@version=5
strategy("RSI Mean Reversion", overlay=true)

length = input.int(defval=14, title="RSI Length")
overbought = input.int(defval=70, title="Overbought Level")
oversold = input.int(defval=30, title="Oversold Level")
qty = input.int(defval=1, title="Order Size")

rsiValue = ta.rsi(close, length)

if rsiValue < oversold
    strategy.entry("Long", strategy.long, qty=qty)

if rsiValue > overbought
    strategy.close("Long")
```

Supported: variables, `input.*`, `ta.*` indicator functions, `strategy.entry`/`strategy.close`, `if`/`else`, arithmetic and comparison operators.
