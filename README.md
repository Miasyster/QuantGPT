<div align="center">

# QuantGPT

**Agent-Driven Factor Research Engine — Autonomous Mining, Evaluation & Validation**

LLM Agent 自治因子挖矿 → 批量回测 → 多维评分 → 反过拟合验证 → Walk-forward 样本外验证 | 全程零人工干预

[![CI](https://github.com/Miasyster/quantgpt/actions/workflows/ci.yml/badge.svg)](https://github.com/Miasyster/quantgpt/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React_18-TypeScript-61DAFB?logo=react&logoColor=white)](https://react.dev)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[Quick Start](docs/QUICKSTART.md) ·
[Architecture](docs/ARCHITECTURE.md) ·
[API Docs](docs/API_DOC.md) ·
[MCP Guide](docs/MCP_GUIDE.md) ·
[Factor Mining](docs/FACTOR_MINING.md) ·
[Roadmap](ROADMAP.md) ·
[Contributing](CONTRIBUTING.md)

**WeChat / 微信交流群**: Add `quantgpt_ai` on WeChat to join the community

<a href="https://star-history.com/#Miasyster/QuantGPT&Date">
  <img src="docs/images/star-history.png" width="600" alt="Star History Chart" />
</a>

</div>

---

## What Is QuantGPT

QuantGPT is an **agent-driven factor research engine** — not a backtest library, not a chatbot wrapper. It gives an LLM Agent (Claude, via MCP) a complete toolkit to autonomously discover, evaluate, iterate, and validate alpha factors, with zero human intervention per research cycle.

The core architecture:

```
LLM Agent (Claude Code / Claude Desktop)
    │
    ├── MCP Tools (15 个)         ← Agent 的工具箱
    │   ├── run_backtest           ← 全市场分组回测
    │   ├── score_factor           ← 0-100 综合评分
    │   ├── diagnose_factor        ← 失败模式诊断
    │   ├── run_anti_overfit       ← 4 项反过拟合检验
    │   ├── run_rolling_validation ← Walk-forward 验证
    │   ├── validate_expression    ← 语法校验
    │   ├── list_operators         ← 60+ 算子文档
    │   ├── list_universes         ← 股票池和基准
    │   ├── wq_brain_submit        ← WQ BRAIN 单因子提交
    │   ├── wq_brain_batch_submit  ← 批量参数扫描提交
    │   ├── wq_brain_submit_by_ids ← 按 ID 提交
    │   ├── wq_brain_list_alphas   ← 查询已提交 alpha
    │   ├── wq_brain_check_alphas  ← 检查 alpha 状态
    │   └── wq_brain_finalize_submissions ← 最终提交确认
    │
    ├── Evolution Engine           ← 因子进化引擎
    │   ├── MutationEngine (8 方向突变)
    │   ├── CrossoverEngine (高分因子交叉)
    │   ├── MetaEvolutionSelector (自适应策略)
    │   └── TrajectoryAnalyzer (轨迹分析)
    │
    ├── WQ BRAIN Integration       ← WorldQuant 直连（可选）
    │   ├── Dollar-neutral 模拟
    │   └── 一键正式提交
    │
    └── Knowledge Base             ← 跨会话知识积累
        ├── rules/    (已验证规则)
        ├── findings/ (经验发现)
        └── failures/ (已证伪路径)
```

### How It Differs from "AI Backtest Tools"

传统工具（包括 ChatGPT + 回测库）的模式是：**人类想因子 → 工具跑回测 → 人类看结果**。Agent 是执行者，人类是决策者。

QuantGPT 的模式是：**人类定义目标 → Agent 自治研究 → 人类审阅产出**。Agent 是研究者，人类是审阅者。

这不是接口层的区别（自然语言 vs. 代码），而是**决策权**的区别。Agent 自主决定：探索哪个方向、生成什么表达式、评估哪些指标、何时迭代、何时放弃、何时提交。

### Production Track Record

| Metric | Value |
|:-------|:------|
| 累计回测任务 | **370+** |
| 单轮迭代（8 候选因子） | **~15 分钟** |
| 表达式算子 | **60+（含非线性、三元、技术指标）** |
| WQ BRAIN 提交（可选） | **3 个因子 IS 全部 PASS，已提交（最佳 Fitness 1.26）** |

---

## Validated Results

QuantGPT 产出的因子先经过本地多维评分 + 反过拟合验证，高质量因子还可选提交至 WQ BRAIN。以下 3 个因子已通过 WQ BRAIN IS 检测并正式提交：

| Factor | Expression | WQ Sharpe | WQ Fitness | WQ Returns | IS Tests | Status |
|:-------|:-----------|:---------:|:----------:|:----------:|:--------:|:------:|
| **Debt-Momentum Composite** | `-1 * rank(ts_av_diff(close, 10)) + rank(debt / enterprise_value)` | **1.77** | **1.26** | **20.18%** | **ALL PASS** | **Submitted** |
| **VWAP Decay Reversal** | `-1 * rank(ts_decay_linear(close / vwap, 10))` | **1.69** | **1.07** | **18.63%** | **ALL PASS** | **Submitted** |
| **Returns-Volume Momentum** | `-1 * rank(ts_decay_linear(returns * volume / adv20, 5))` | **1.60** | **1.03** | **24.15%** | **ALL PASS** | **Submitted** |

> 3 个因子代表不同的 alpha 来源：**Debt-Momentum** 结合动量反转与基本面（债务/企业价值），行业中性化；**VWAP Decay Reversal** 捕捉价格偏离 VWAP 的衰减回归，市场中性化；**Returns-Volume Momentum** 捕捉收益率与相对成交量的衰减动量，市场中性化。全程 Agent 自治完成。

<p align="center">
  <img src="example_factor/1-1.png" width="49%" alt="WQ BRAIN PnL — Debt-Momentum Composite (Submitted)" />
  <img src="example_factor/1-2.png" width="49%" alt="WQ BRAIN IS Summary — Debt-Momentum Composite (Submitted)" />
</p>
<p align="center">
  <sub>Debt-Momentum Composite — 已正式提交：Sharpe 1.77, Fitness 1.26, Returns 20.18%, IS 全部 PASS</sub>
</p>

<p align="center">
  <img src="example_factor/2-1.png" width="49%" alt="WQ BRAIN PnL — VWAP Decay Reversal (Submitted)" />
  <img src="example_factor/2-2.png" width="49%" alt="WQ BRAIN IS Summary — VWAP Decay Reversal (Submitted)" />
</p>
<p align="center">
  <sub>VWAP Decay Reversal — 已正式提交：Sharpe 1.69, Fitness 1.07, Returns 18.63%, IS 全部 PASS</sub>
</p>

<p align="center">
  <img src="example_factor/3-1.png" width="49%" alt="WQ BRAIN PnL — Returns-Volume Momentum (Submitted)" />
  <img src="example_factor/3-2.png" width="49%" alt="WQ BRAIN IS Summary — Returns-Volume Momentum (Submitted)" />
</p>
<p align="center">
  <sub>Returns-Volume Momentum — 已正式提交：Sharpe 1.60, Fitness 1.03, Returns 24.15%, IS 全部 PASS</sub>
</p>

---

## Autonomous Factor Mining — The Core Loop

> **This is QuantGPT's defining capability.**
>
> Agent 读知识库、设计假设、批量实验、分析结果、积累知识、自我迭代，每个结论经过双模型交叉验证。

### Research Cycle

```
                    ┌─────────────────────────────┐
                    │  Research Notes & Knowledge  │
                    │  (Rules / Findings / Fails)  │
                    └──────────┬──────────────────┘
                               │ read
                               ▼
┌──────────┐    ┌──────────────────────────┐    ┌──────────────────┐
│  Phase 0 │───▶│  Phase 1: Factor Design  │───▶│  Phase 2: Batch  │
│  Context │    │  Hypothesis → Expression │    │  Backtest (10-20 │
│  Loading │    │  1-3 candidates per idea │    │  concurrent)     │
└──────────┘    └──────────────────────────┘    └────────┬─────────┘
                                                         │
                               ┌─────────────────────────┘
                               ▼
                ┌──────────────────────────────────────────┐
                │  Phase 3: Four-Step Analysis             │
                │                                          │
                │  ① Fact Collection (metrics vs baseline) │
                │  ② Independent Judgment (Agent)          │
                │  ③ Cross-Review (DeepSeek Reasoner)      │
                │  ④ Consensus or Divergence Resolution    │
                └──────────────────┬───────────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
          ┌─────────────────┐          ┌──────────────────┐
          │  Phase 4: Update │          │  Phase 5: Stop?  │
          │  Notes + Knowledge│         │  Converged /     │
          │  Base             │◀────────│  Time / Rounds   │
          └─────────────────┘          └──────────────────┘
                    │                         │ no
                    │                         └──▶ back to Phase 1
                    ▼
          ┌──────────────────┐
          │  Phase 6: Report │
          │  A/B factors +   │
          │  new knowledge   │
          └──────────────────┘
```

### Key Mechanisms

<table>
<tr>
<td width="50%">

**Dual-LLM Cross-Review**

每个结论性判断（采用/不采用/关闭方向）必须经过第二个 LLM 独立评审。把事实数据和第一个模型的推理链一起发给 DeepSeek Reasoner，要求独立评估推理是否合理、是否有遗漏角度。

共识 → 直接输出。分歧 → 呈现双方证据，采用更保守结论。

这解决了单模型因子研究的核心问题：**confirmation bias**。

</td>
<td width="50%">

**Persistent Knowledge Base**

```
research_notes/knowledge/
├── rules/       ← 已验证的稳定规则（必须遵守）
├── findings/    ← 经验发现（参考）
└── failures/    ← 已证伪路径（禁止重复）
```

知识库跨会话积累。第 10 次研究会话可以直接利用前 9 次的所有发现，避免重复实验，遵守已验证规则，绕开已证伪路径。

这不是 chat history——是**结构化的研究资产**。

</td>
</tr>
<tr>
<td>

**Batch Concurrent Evaluation**

单次提交 10-20 个因子表达式，并发回测 + 三波重试。结果按 fitness 降序排列。hs300 fitness < 0.1 时自动跳过 csi500 验证，节省算力。

```python
from scripts.factor_miner import batch_evaluate
results = batch_evaluate(
    server, expressions, params,
    max_concurrent=10
)
```

</td>
<td>

**Research Discipline (Enforced)**

不是建议，是硬性规则：
- 每次实验只改一个变量
- 提交前检查是否已做过（笔记 + 知识库）
- 分析结论标注"仅为假设"
- 失败实验同样记录原因
- 表达式 > 4 层嵌套需额外论证
- 简单清晰 > 复杂精巧

</td>
</tr>
</table>

> **上面 Validated Results 中的因子就是这个流程的产出。** 多轮迭代产出。完整方法论见 [Factor Mining Guide](docs/FACTOR_MINING.md)。

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                     QuantGPT Research Engine                       │
├─────────────┬──────────────────────────────┬───────────────────────┤
│             │         Core Engine          │                       │
│  Agent      │  ┌──────────────────────┐   │   Data Layer          │
│  Interface  │  │  Expression Parser   │   │  ┌─────────────────┐  │
│             │  │  60+ operators       │   │  │ baostock (free)  │  │
│ MCP Tools   │  │  WQ compatible       │   │  │ akshare (free)   │  │
│ REST API    │  └──────────┬───────────┘   │  │ PolarDB (opt)    │  │
│ Web UI      │  ┌──────────▼───────────┐   │  │ Parquet cache    │  │
│ (monitor)   │  │  Backtest Engine     │   │  └─────────────────┘  │
│             │  │  Rank-based grouping │   │                       │
│             │  │  IC / group returns  │   │   AI Layer            │
│             │  └──────────┬───────────┘   │  ┌─────────────────┐  │
│             │  ┌──────────▼───────────┐   │  │ DeepSeek LLM    │  │
│             │  │  Validation Suite    │   │  │ Factor design   │  │
│             │  │  Anti-overfit (4x)   │   │  │ Cross-review    │  │
│             │  │  Walk-forward        │   │  │ Mutation engine │  │
│             │  │                      │   │  └─────────────────┘  │
│             │  │  WQ BRAIN (optional) │   │                       │
│             │  └──────────────────────┘   │   Storage             │
│             │                             │  ┌─────────────────┐  │
│             │  Evolution Engine           │  │ SQLite (default)│  │
│             │  Trajectory → Meta-Evo →    │  │ PostgreSQL (opt)│  │
│             │  Mutation / Crossover       │  └─────────────────┘  │
├─────────────┴──────────────────────────────┴───────────────────────┤
│  Agent Orchestrator: Claude Code skill loop / Claude Desktop MCP   │
└────────────────────────────────────────────────────────────────────┘
```

### Tech Stack

| Layer | Technology |
|:------|:-----------|
| Agent | Claude Code (skill loop) / Claude Desktop (MCP) |
| Backend | Python 3.10+, FastAPI, uvicorn, SQLAlchemy 2.0 async |
| Database | SQLite (default, zero-config) / PostgreSQL (optional) |
| AI/LLM | DeepSeek (factor generation + cross-review) |
| Market Data | baostock + akshare (free) → Parquet cache |
| Frontend | React 18 + TypeScript + Tailwind CSS 4 (monitoring dashboard) |
| MCP | FastMCP (stdio / SSE / streamable-http) |
| Report | QuantStats HTML |

---

## Key Engineering Decisions

### 1. Expression Parser — The Core Differentiator

自研的表达式解析器（`expression_parser.py`, 1000+ lines）是整个系统的核心：

- **60+ 算子**：截面（`rank`, `zscore`）、时序（`ts_corr`, `decay_linear`）、非线性（`sigmoid`, `tanh`, `sign_power`）、条件/三元（`where`, `trade_when`, `? :`）、技术指标（`rsi`, `macd`, `atr`）
- **双模式**：`mode="local"` 开放全部 60+ 算子，`mode="wq"` 仅允许 WQ BRAIN 兼容子集（可选提交前校验）
- **语义正确的截面/时序分离**：`rank()` 按 `trade_date` 分组（截面），`ts_mean()` 按 `stock_code` 分组（时序），自动处理分组逻辑
- **安全约束**：递归深度限制、窗口上限、表达式长度限制，防止恶意输入

### 2. Three-Layer Anti-Overfit System

| Layer | Module | Method |
|:------|:-------|:-------|
| Statistical Tests | `anti_overfit.py` | IC 稳定性 + 子样本压力测试（牛/熊/震荡）+ 安慰剂检验 + 半衰期估计 |
| Walk-Forward | `rolling_validator.py` | 滚动 train/valid/test 窗口，评估样本外 IC 衰减 |
| WQ Simulation | `wq_simulate.py` | Dollar-neutral 多空模拟，Sharpe/Turnover/Fitness 计算 |
| WQ BRAIN API (optional) | `wq_brain_client.py` | 直连 BRAIN 平台 — 真实模拟 + 一键正式提交 |

### 3. Evolutionary Factor Iteration

受 QuantaAlpha 启发的三阶段自动搜索：

```
TrajectoryAnalyzer → MetaEvolutionSelector → Strategy Execution
 (质量指标评估)       (EXPLOIT/EXPLORE/        (MutationEngine ×8 方向
                      RECOMBINE/SIMPLIFY)       / CrossoverEngine)
```

8 种定向突变：时间窗口变异、算子替换、复杂度调整、截面变换叠加等。5 维评分驱动迭代方向。

### 4. Agent-First Access Model

| Mode | Role | Use Case |
|:-----|:-----|:---------|
| **MCP (primary)** | Agent toolkit | Claude Code / Claude Desktop 通过 MCP 调用所有研究工具，驱动自治研究循环 |
| **REST API** | Programmatic access | 批量回测、外部系统集成、CI/CD 因子验证 |
| **Web UI** | Monitoring dashboard | 任务监控、报告查看、因子库管理 |

<details>
<summary><b>MCP Tools (15 个)</b></summary>

| Tool | Description |
|:-----|:------------|
| `list_operators` | 全部算子文档 |
| `list_universes` | 股票池和基准 |
| `validate_expression` | 语法校验 |
| `run_backtest` | 完整回测 |
| `score_factor` | 评分（0–100, A/B/C/D） |
| `diagnose_factor` | 失败模式诊断 + 改进建议 |
| `run_anti_overfit` | 4 项反过拟合检验 |
| `run_rolling_validation` | Walk-forward 验证 |

</details>

---

## Competitive Landscape

| Capability | JoinQuant | Backtrader | ChatGPT + Backtest | **QuantGPT** |
|:-----------|:------:|:------:|:------:|:------:|
| Research mode | Human writes code | Human writes code | Human prompts, tool executes | **Agent autonomously researches** |
| Factor discovery | Manual | Manual | One-shot LLM | **Multi-round evolution + knowledge base** |
| Anti-bias | Researcher judgment | None | None | **Dual-LLM mandatory cross-review** |
| Knowledge accumulation | Personal notes | None | Lost between sessions | **Structured KB across sessions** |
| Anti-overfit | -- | -- | -- | **4 statistical tests + walk-forward** |
| WQ BRAIN (optional) | -- | -- | -- | Operator-aligned + direct submission |
| MCP / AI Agent | -- | -- | -- | **15 tools, skill-loop orchestration** |
| Live trading | Yes | Limited | -- | -- |
| Intraday data | Yes | Yes | -- | Daily only |

---

## Quick Start

### Option 1: Agent Mode (Recommended)

```bash
git clone https://github.com/Miasyster/QuantGPT.git && cd QuantGPT
make setup   # creates venv, installs deps, generates .env
make run     # starts server at http://localhost:8003
```

Add MCP configuration to Claude Code or Claude Desktop:

```json
{
  "mcpServers": {
    "quantgpt": {
      "command": "python",
      "args": ["-m", "quantgpt"]
    }
  }
}
```

Then let the Agent work: *"在沪深300上挖掘高质量因子"*

### Option 2: Expression Mode (No LLM Required)

```bash
# Direct expression backtest via API
curl -X POST http://localhost:8003/api/v1/auto_backtest \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"expression": "rank(close / ts_mean(close, 20))", "universe": "hs300"}'
```

### Windows Quick Start

Windows 用户不需要 `make` 和 `restart.sh`，手动执行即可：

```powershell
# 1. 克隆项目
git clone https://github.com/Miasyster/QuantGPT.git
cd QuantGPT

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\activate
pip install -e .
# 可选：装 PostgreSQL 支持
# pip install -e ".[postgresql]"

# 3. 构建前端（需要 Node.js，从 nodejs.org 下载 LTS 版本）
cd frontend && npm install && npm run build && cd ..

# 4. 启动服务
python -m quantgpt --transport http
# 浏览器打开 http://localhost:8003
```

> **注意：**
> - 推荐 Python 3.11 或 3.12（3.14 太新，部分依赖可能不兼容）
> - 如果端口被占用：`netstat -ano | findstr :8003` 查进程，`taskkill /PID <pid> /F` 杀掉
> - 也可以使用 WSL2（`wsl --install`），体验与 macOS/Linux 完全一致

**Zero config by default**: SQLite database, baostock + akshare free data. See [full Quick Start guide](docs/QUICKSTART.md) for details.

<details>
<summary><b>Optional: DeepSeek API (for factor generation & cross-review)</b></summary>

```bash
# Edit .env, add your DeepSeek API key (~$0.001 per query)
DEEPSEEK_API_KEY=sk-your-key-here
```

</details>

<details>
<summary><b>Optional: PostgreSQL (for production)</b></summary>

```bash
pip install "quantgpt[postgresql]"
# Edit .env:
DATABASE_URL=postgresql+asyncpg://quantgpt:password@localhost:5432/quantgpt
alembic upgrade head
```

</details>

<details>
<summary><b>Expression Examples</b></summary>

**Local backtest** — works out of the box with baostock/akshare data:

```python
# 20-day momentum
rank(close / ts_mean(close, 20))

# Low volatility
rank(-1 * ts_std(close/ts_shift(close,1)-1, 20))

# Decay-weighted correlation
decay_linear(rank(ts_corr(vwap, volume, 10)), 5)

# Momentum + profitability composite
-1 * rank(ts_av_diff(close, 10)) + rank(roe)
```

**WQ BRAIN remote (optional)** — these expressions use fields only available via WQ BRAIN submission:

```python
# Debt-momentum composite — BRAIN submitted, Fitness 1.26, Sharpe 1.77
-1 * rank(ts_av_diff(close, 10)) + rank(debt / enterprise_value)

# VWAP decay reversal — BRAIN submitted, Fitness 1.07, Sharpe 1.69
-1 * rank(ts_decay_linear(close / vwap, 10))

# Returns-volume momentum — BRAIN submitted, Fitness 1.03, Sharpe 1.60
-1 * rank(ts_decay_linear(returns * volume / adv20, 5))
```

</details>

---

## Project Structure

```
quantgpt/
├── quantgpt/                    # Backend (Python)
│   ├── expression_parser.py     # Factor expression parser (60+ ops, WQ compatible)
│   ├── backtest.py              # Rank-based group backtest engine
│   ├── market_data.py           # baostock/akshare → Parquet cache
│   ├── api_server.py            # FastAPI REST API + SSE
│   ├── mcp_server.py            # FastMCP server (15 tools — Agent's toolkit)
│   ├── iteration.py             # 3-phase evolutionary iteration
│   ├── mutation_engine.py       # 8 directed mutation strategies
│   ├── crossover_engine.py      # High-score factor crossover
│   ├── meta_evolution.py        # Adaptive strategy selector
│   ├── trajectory_analyzer.py   # Trajectory quality metrics
│   ├── anti_overfit.py          # 4 statistical anti-overfit tests
│   ├── rolling_validator.py     # Walk-forward validation
│   ├── wq_simulate.py           # Dollar-neutral simulator
│   ├── wq_brain_client.py       # WQ BRAIN API (optional)
│   ├── neutralize.py            # Industry & cap neutralization
│   ├── daily_summary.py         # LLM-powered daily market report
│   └── routes/                  # API route modules
├── frontend/                    # React 18 + TypeScript + Tailwind CSS 4
│   └── src/components/          # Monitoring dashboard
├── scripts/
│   └── factor_miner.py          # Batch factor evaluation toolkit
├── tests/                       # 74 tests (parser + backtest + WQ simulate)
├── example_factor/              # BRAIN validation screenshots
└── docs/                        # Architecture, API, MCP, Mining guides
```

---

## Limitations

- **Daily frequency only** — no intraday backtesting
- **A-share market only** — China mainland equities
- **Agent quality depends on LLM** — better models produce better factors

---

## License

[MIT](LICENSE) — Copyright (c) 2026 Miasyster

This repository is the **original source** of the QuantGPT factor research engine.
Derivative works should retain the copyright notice and comply with the MIT License terms.
See [NOTICE](NOTICE) for details.

<sub>*Past factor performance does not guarantee future returns. This project does not constitute investment advice.*</sub>
