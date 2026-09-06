# QuantGPT MCP 配置指南

QuantGPT 提供标准 MCP (Model Context Protocol) 接口，支持 10 个因子研究工具。可通过 Claude Code、Claude Desktop 等 MCP 客户端直接调用。

## OpenAI Codex CLI（Windows，推荐 Streamable HTTP）

Codex 支持 stdio 和 Streamable HTTP。QuantGPT 的行情依赖（尤其 baostock）会输出普通文本，
因此 Codex 推荐使用独立的 HTTP 入口，将协议流与进程 stdout 完全隔离：

```powershell
# 在 QuantGPT 项目根目录运行；请替换为安装了本项目依赖的 Python 绝对路径
C:\path\to\python.exe -m quantgpt.codex_mcp_server --host 127.0.0.1 --port 8003

# 另开 PowerShell 注册并检查服务
codex.cmd mcp add quantgpt --url http://127.0.0.1:8003/mcp
codex.cmd mcp list
codex.cmd mcp get quantgpt
```

长回测需要在 `%USERPROFILE%\.codex\config.toml`（或可信项目的 `.codex\config.toml`）中设置工具超时：

```toml
[mcp_servers.quantgpt]
url = "http://127.0.0.1:8003/mcp"
startup_timeout_sec = 30
tool_timeout_sec = 600
```

最小验证调用：

```powershell
C:\path\to\python.exe scripts\test_codex_mcp.py --transport http --timeout 600
```

或在 Codex 中发起同样的工具调用：

```text
调用 quantgpt 的 score_factor：
expression="rank(close / ts_shift(close, 20) - 1)",
universe="small_scale",
start_date="2024-01-01",
end_date="2025-12-31"
```

也可继续用 stdio。QuantGPT 的 stdio compatibility layer 会保留原始 stdout 给 JSON-RPC，
并把 baostock 的 `login success!` / `logout success!` 等普通输出转到 stderr：

```powershell
codex.cmd mcp add quantgpt `
  --env QUANTGPT_TASK_BACKEND=thread `
  -- C:\path\to\python.exe -m quantgpt
```

stdio 模式同样应在 `config.toml` 中设置 `tool_timeout_sec = 600`。Windows 推荐显式使用
`QUANTGPT_TASK_BACKEND=thread`；process backend 也已加入口保护与 worker stdout 隔离。

## 快速开始（推荐）

### Claude Code

在项目根目录添加 `.mcp.json`（stdio 模式，已验证可用）：

```json
{
  "mcpServers": {
    "quantgpt": {
      "type": "stdio",
      "command": "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
      "args": ["-m", "quantgpt"],
      "cwd": "/absolute/path/to/quantgpt"
    },
    "deepseek": {
      "type": "stdio",
      "command": "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
      "args": ["scripts/mcp_deepseek.py"],
      "cwd": "/absolute/path/to/quantgpt"
    }
  }
}
```

**关键要点：**

1. **必须用 stdio 模式** — Claude Code 对 `streamable-http` / `sse` 类型支持不稳定，stdio 最可靠
2. **command 必须用 Python 绝对路径** — 如 `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3`，不要用 `python3`（Claude Code 的子进程环境可能找不到）
3. **cwd 必须用绝对路径** — 指向项目根目录，确保 `python3 -m quantgpt` 能找到包
4. **deepseek MCP 需要 `.env` 中配置 `DEEPSEEK_API_KEY`** — 脚本会自动从 `.env` 读取

配置完成后**重启 Claude Code**（退出后重新进入项目目录），验证连接：

```bash
# 在 Claude Code 中输入
/mcp
# 应显示 quantgpt: connected, deepseek: connected
```

### Claude Desktop

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）：

```json
{
  "mcpServers": {
    "quantgpt": {
      "command": "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
      "args": ["-m", "quantgpt"],
      "cwd": "/absolute/path/to/quantgpt"
    }
  }
}
```

### 从 GitHub 安装

```bash
# 克隆项目
git clone https://github.com/Miasyster/QuantGPT.git
cd QuantGPT

# 安装依赖
pip install -e .

# 配置（在 .env 中设置 DeepSeek API Key，米筐账号可选）
cp .env.example .env
# 编辑 .env

# .mcp.json 已包含在仓库中，重启 Claude Code 即自动连接
```

### 常见问题

**Q: MCP 连不上？**

1. 确认 `command` 是绝对路径，运行 `which python3` 获取
2. 确认 `cwd` 指向项目根目录（包含 `quantgpt/` 子目录的那层）
3. 确认 `pip install -e .` 已执行（quantgpt 包已安装）
4. 修改 `.mcp.json` 后必须重启 Claude Code

**Q: HTTP 模式（streamable-http）能用吗？**

MCP 同时挂载在 HTTP 服务上（`/mcp/` 和 `/mcp-sse/`），但需要先启动 HTTP 服务（`bash restart.sh`），且 `mcp_server.py` 的 `allowed_hosts` 必须包含带端口的 host（如 `localhost:8003`）。stdio 模式无此限制，推荐优先使用。

---

## 工具列表

| 工具 | 说明 |
|------|------|
| `list_operators` | 返回全部因子表达式算子及用法 |
| `list_universes` | 返回可用股票池和基准指数 |
| `validate_expression` | 验证因子表达式语法 |
| `run_backtest` | 执行因子回测，生成 HTML 报告 |
| `score_factor` | 因子综合评分 (0-100, A/B/C/D) |
| `diagnose_factor` | 诊断因子问题，推荐改进策略 |
| `run_anti_overfit` | 反过拟合检测 (4 项测试) |
| `run_rolling_validation` | 滚动验证 (Walk-Forward) |
| `wq_brain_submit` | 提交因子到 WorldQuant BRAIN |
| `ask_deepseek` | 调用 DeepSeek LLM 进行研究评审（独立 MCP） |

### 通用参数

以下参数在 `run_backtest`、`score_factor`、`run_anti_overfit`、`run_rolling_validation` 中通用：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `expression` | str | 必填 | 因子表达式 |
| `universe` | str | `hs300` | 股票池：`hs300` / `csi500` / `csi1000` / `csi2000` / `small_scale` |
| `start_date` | str | `2023-01-01` | 回测起始日期 |
| `end_date` | str | `2025-12-31` | 回测结束日期 |
| `n_groups` | int | `5` | 分组数量 |
| `holding_period` | int | `5` | 持仓周期（交易日） |
| `benchmark` | str | `hs300` | 基准指数：`hs300` / `zz500` / `sz50` |
| `neutralize_industry` | bool | `true` | 行业中性化 |
| `neutralize_cap` | bool | `true` | 市值中性化 |

---

## 使用示例

### Agent 工作流

```
1. list_operators         → 了解可用算子
2. 构造因子表达式
3. validate_expression    → 确认语法正确
4. score_factor           → 快速评分
5. run_backtest           → 完整回测 + HTML 报告
6. diagnose_factor        → 诊断改进方向
7. run_anti_overfit       → 检查过拟合风险
8. run_rolling_validation → 样本外验证
```

### 常用因子表达式

```python
# 20日动量
rank(close / ts_mean(close, 20))

# 成交量异动
rank(volume / ts_mean(volume, 10))

# 波动率因子
ts_std(close / ts_shift(close, 1) - 1, 20)

# 反转因子
rank(-1 * ts_delta(close, 5) / ts_shift(close, 5))

# 量价背离
rank(ts_corr(close, volume, 10))

# ROE 动量（基本面）
rank(ts_delta(roe, 60))
```

---

## 股票池

| 名称 | 说明 | 成分股数量 |
|------|------|-----------|
| `small_scale` | 蓝筹测试池 | 5 |
| `hs300` | 沪深300 | ~300 |
| `csi500` | 中证500 | ~500 |
| `csi1000` | 中证1000 | ~1000 |
| `csi2000` | 中证2000 | ~2000 |

---

## 数据源

- **akshare / baostock**：免费数据源，回测流程默认使用，自动缓存到 `data/stocks/*.parquet`
- **rqdatac（米筐）**：仅手动触发（admin 端点 / prewarm 脚本），需在 `.env` 中配置 `RQDATAC_USERNAME` 和 `RQDATAC_PASSWORD`
- 首次使用会自动下载并缓存数据，后续直接读取

---

## HTTP 服务模式（可选）

启动 HTTP 服务后，MCP 自动挂载到两个端点：

```bash
bash restart.sh   # 启动 HTTP 服务（端口 8003）
```

| 端点 | 协议 | 说明 |
|------|------|------|
| `/mcp/` | streamable-http | 推荐（需 `Accept: application/json, text/event-stream`） |
| `/mcp-sse/` | SSE | 兼容旧客户端 |

`mcp_server.py` 中的 `allowed_hosts` 需包含带端口的 host：

```python
allowed_hosts=["localhost", "localhost:8003", "127.0.0.1", "127.0.0.1:8003"]
```

> stdio 模式（`.mcp.json` 配置）不依赖 HTTP 服务运行，是 Claude Code 的推荐方式。
