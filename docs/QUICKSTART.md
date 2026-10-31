# Quick Start — 5 分钟跑通第一个因子回测

## Prerequisites

- Python 3.10+
- Node.js 20+ (optional, for frontend dashboard)

## 1. Clone & Setup

```bash
git clone https://github.com/Miasyster/QuantGPT.git
cd QuantGPT
make setup
```

This creates a virtual environment, installs all dependencies, and generates `.env` from the template.

**No API keys needed** for expression-only mode.

## 2. Start the Server

```bash
bash restart.sh
```

The server starts at `http://localhost:8003`.

## 3. Agent Mode (Recommended)

Add MCP configuration to Claude Code or Claude Desktop:

```json
{
  "mcpServers": {
    "quantgpt": {
      "type": "stdio",
      "command": "python3",
      "args": ["-m", "quantgpt"],
      "cwd": "/path/to/QuantGPT"
    }
  }
}
```

Then let the Agent work:
```
在沪深300上挖掘高 fitness 的因子，目标 WQ BRAIN 可提交
```

## 4. Expression Mode (No LLM Required)

Via API:
```bash
curl -X POST http://localhost:8003/api/v1/auto_backtest \
  -H "Content-Type: application/json" \
  -d '{"expression": "rank(close / ts_mean(close, 20))", "universe": "hs300"}'
```

Or enter a factor expression directly in the web UI at `http://localhost:8003`.

## 5. Try More Expressions

```
# Debt-momentum composite (submitted, Fitness 1.26, Sharpe 1.77)
-1 * rank(ts_av_diff(close, 10)) + rank(debt / enterprise_value)

# VWAP decay reversal (submitted, Fitness 1.07, Sharpe 1.69)
-1 * rank(ts_decay_linear(close / vwap, 10))

# Returns-volume momentum (submitted, Fitness 1.03, Sharpe 1.60)
-1 * rank(ts_decay_linear(returns * volume / adv20, 5))

# Volume anomaly
rank(volume / ts_mean(volume, 10))

# Value factor (needs fundamental data)
rank(-1 * pe)
```

## 6. Enable DeepSeek (Optional, for factor generation & cross-review)

1. Get a DeepSeek API key from [platform.deepseek.com](https://platform.deepseek.com)
2. Edit `.env`:
   ```
   DEEPSEEK_API_KEY=sk-your-key-here
   ```
3. Restart: `bash restart.sh`

## What's Next

- Read [ARCHITECTURE.md](ARCHITECTURE.md) for system design
- Check [MCP_GUIDE.md](MCP_GUIDE.md) for MCP tool details
- Read [FACTOR_MINING.md](FACTOR_MINING.md) for the autonomous research loop
- Browse `example_factor/` for validated factor results with WQ BRAIN screenshots
