# QuantGPT API 完整文档

> 版本: v1 | 基础路径: `/api/v1` | 认证: Bearer Token (JWT)

---

## 目录

1. [启动与配置](#启动与配置)
2. [认证](#认证)
3. [会话管理](#会话管理)
4. [回测](#回测)
5. [实时推送 (SSE)](#实时推送-sse)
6. [迭代优化](#迭代优化)
7. [报告](#报告)
8. [反馈](#反馈)
9. [管理后台](#管理后台)
10. [MCP Tools](#mcp-tools)
11. [健康检查](#健康检查)
12. [错误码](#错误码)
13. [股票池与基准](#股票池与基准)
14. [因子表达式语法](#因子表达式语法)

---

## 启动与配置

### 启动命令

```bash
# HTTP 服务 (含前端)
bash restart.sh   # 默认端口 8003

# MCP 服务 (stdio)
python -m quantgpt

# 数据预热
python -m quantgpt --prefetch hs300 csi500
```

### 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `DATABASE_URL` | 是 | — | PostgreSQL 连接串 |
| `LLM_API_KEY` | 否 | — | LLM API Key，未设置时回退到 `DEEPSEEK_API_KEY` |
| `LLM_BASE_URL` | 否 | `https://api.deepseek.com/v1` | LLM API 地址，未设置时回退到 `DEEPSEEK_BASE_URL` |
| `LLM_MODEL` | 否 | `deepseek-chat` | 模型名称，未设置时回退到 `DEEPSEEK_MODEL` |
| `JWT_SECRET` | 是 | — | JWT 签名密钥 |
| `QUANTGPT_CORS_ORIGINS` | 否 | `*` | CORS 允许源,逗号分隔 |
| `QUANTGPT_ADMIN_PASSWORD` | 否 | — | 管理后台密码 |
| `QUANTGPT_MAX_ACTIVE_TASKS` | 否 | `5` | 最大并发任务数 |
| `QUANTGPT_TASK_TTL` | 否 | `3600` | 内存任务 TTL (秒) |
| `QUANTGPT_RATE_LIMIT` | 否 | `10` | 每分钟请求限制 |
| `QUANTGPT_MAX_PROMPT_LEN` | 否 | `500` | Prompt 最大长度 |
| `QUANTGPT_FEEDBACK_WEBHOOK` | 否 | — | 飞书 Webhook URL |
| `QUANTGPT_FEEDBACK_WEBHOOK_SECRET` | 否 | — | 飞书签名密钥 |

---

## 认证

> 当前版本已移除认证：所有 API 无需 Token，请求统一按本地开发用户处理。下文的 `Authorization` 请求头和 `/auth/*` 登录流程可忽略。

### POST /api/v1/auth/send-code

发送邮箱验证码。

**请求体:**

```json
{
  "email": "user@example.com"
}
```

**响应 200:**

```json
{
  "message": "验证码已发送",
  "expires_in": 300
}
```

**错误:** 429 (发送过于频繁), 400 (邮箱格式错误)

---

### POST /api/v1/auth/verify-code

验证码登录/注册。首次登录自动注册。

**请求体:**

```json
{
  "email": "user@example.com",
  "code": "123456"
}
```

**响应 200:**

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "created_at": "2026-01-01T00:00:00"
  }
}
```

**错误:** 400 (验证码错误/过期), 429

---

### POST /api/v1/auth/refresh

刷新 Access Token。

**请求体:**

```json
{
  "refresh_token": "eyJ..."
}
```

**响应 200:**

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer"
}
```

---

### GET /api/v1/auth/me

获取当前用户信息。

**响应 200:**

```json
{
  "id": "uuid",
  "email": "user@example.com",
  "created_at": "2026-01-01T00:00:00"
}
```

---

## 会话管理

会话用于组织一系列相关的回测任务。

### POST /api/v1/sessions

创建新会话。

**请求体:**

```json
{
  "name": "动量因子研究"
}
```

**响应 201:**

```json
{
  "id": "uuid",
  "name": "动量因子研究",
  "created_at": "2026-01-01T00:00:00"
}
```

---

### GET /api/v1/sessions

列出当前用户的所有会话 (按更新时间倒序)。

**响应 200:**

```json
{
  "sessions": [
    {
      "id": "uuid",
      "name": "动量因子研究",
      "created_at": "2026-01-01T00:00:00",
      "updated_at": "2026-01-02T00:00:00"
    }
  ]
}
```

---

### PATCH /api/v1/sessions/{session_id}

重命名会话。

**请求体:**

```json
{
  "name": "新名称"
}
```

**响应 200:**

```json
{
  "id": "uuid",
  "name": "新名称"
}
```

---

### DELETE /api/v1/sessions/{session_id}

删除会话 (级联删除关联任务)。

**响应 204:** 无内容

---

## 回测

### POST /api/v1/auto_backtest

提交回测任务。支持自然语言描述或直接输入因子表达式。异步执行,立即返回 task_id。

**请求体:**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `prompt` | string | 是 | — | 自然语言描述或因子表达式 |
| `universe` | string | 否 | `hs300` | 股票池: `small_scale` / `hs300` / `csi500` |
| `start_date` | string | 否 | `2023-01-01` | 起始日期 YYYY-MM-DD |
| `end_date` | string | 否 | `2025-12-31` | 结束日期 YYYY-MM-DD |
| `n_groups` | int | 否 | `5` | 分组数量 (2~20) |
| `holding_period` | int | 否 | `5` | 持仓周期 (1~60 交易日) |
| `benchmark` | string | 否 | `hs300` | 基准: `hs300` / `zz500` / `sz50` |
| `session_id` | string | 否 | null | 关联会话 ID |

**请求示例:**

```bash
curl -X POST http://localhost:8003/api/v1/auto_backtest \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "帮我测试一个20日动量因子",
    "universe": "hs300",
    "start_date": "2023-01-01",
    "end_date": "2025-12-31"
  }'
```

**响应 202:**

```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "pending"
}
```

**错误:** 429 (频率限制), 503 (任务已满), 400 (参数错误)

---

### GET /api/v1/tasks

分页查询当前用户的任务列表。

**查询参数:**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `page` | 1 | 页码 |
| `page_size` | 20 | 每页数量 (1~100) |
| `session_id` | — | 按会话过滤 |

**响应 200:**

```json
{
  "tasks": [
    {
      "task_id": "a1b2c3d4e5f6",
      "status": "completed",
      "params": { "...": "..." },
      "expression": "rank(close/ts_mean(close, 20))",
      "result": { "...": "..." }
    }
  ],
  "page": 1,
  "page_size": 20
}
```

---

### GET /api/v1/tasks/{task_id}

查询单个任务状态和结果。

**任务状态流转:**

```
pending → generating_expression → validating → fetching_data
  → backtesting → analyzing → generating_report → completed / failed
```

迭代任务:

```
pending → iterating → iteration_completed / failed
```

**完成响应:**

```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "completed",
  "expression": "rank(close/ts_mean(close, 20))",
  "result": {
    "report_url": "/api/v1/reports/backtest_report_20260321.html",
    "metrics": {
      "total_return": 0.156,
      "cagr": 0.052,
      "sharpe": 0.48,
      "sortino": 0.63,
      "max_drawdown": -0.182,
      "volatility": 0.185,
      "win_rate": 0.524,
      "profit_factor": 1.12
    },
    "backtest_summary": {
      "long_short_sharpe": 0.35,
      "long_short_annual": 0.043,
      "top_group_sharpe": 0.48,
      "monotonicity_score": 0.8,
      "spread": 0.00082,
      "ic_mean": 0.032,
      "rank_ic_mean": 0.038,
      "ic_ir": 0.45,
      "ic_win_rate": 0.56,
      "turnover": 0.42,
      "cost_adjusted": true,
      "cost_rate": 0.003,
      "total_cost_drag": 0.0156,
      "group_returns": {
        "0": { "group": "G1", "mean_return": 0.00021, "annual_return": 0.054, "sharpe": 0.35, "max_drawdown": -0.15 },
        "4": { "group": "G5", "mean_return": 0.00102, "annual_return": 0.293, "sharpe": 0.85, "max_drawdown": -0.12 }
      }
    },
    "anti_overfit": {
      "score": 75.0,
      "recommendation": "谨慎",
      "passed_count": 3,
      "total_count": 4,
      "tests": [
        { "name": "IC稳定性", "passed": true, "details": { "ic_mean": 0.032, "positive_rate": 0.58 } },
        { "name": "子样本压力", "passed": true, "details": { "consistency": 0.8 } },
        { "name": "安慰剂检验", "passed": true, "details": { "perm_pass": true, "decay_ok": true } },
        { "name": "半衰期估计", "passed": false, "details": { "half_life_days": 3.2 } }
      ]
    },
    "stock_factor_data": {
      "rebalance_date": "2025-12-15",
      "flipped": false,
      "total_stock_count": 300,
      "stocks": [
        { "stock_code": "sh.600519", "factor_value": 1.05, "factor_rank": 0.98, "group": 4, "group_label": "G5", "period_return": 0.12 }
      ]
    },
    "params": {
      "expression": "rank(close/ts_mean(close, 20))",
      "universe": "hs300",
      "start_date": "2023-01-01",
      "end_date": "2025-12-31",
      "n_groups": 5,
      "holding_period": 5,
      "benchmark": "hs300",
      "stock_count": 300
    },
    "llm": {
      "prompt": "帮我测试一个20日动量因子",
      "generated_expression": "rank(close/ts_mean(close, 20))"
    }
  }
}
```

**失败响应:**

```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "failed",
  "error": "因子表达式无效: Unknown column"
}
```

---

## 实时推送 (SSE)

### GET /api/v1/tasks/{task_id}/stream

Server-Sent Events 实时推送任务状态变化。

**认证:** 通过 query param `?token=<access_token>` (EventSource 不支持 Header)

**事件类型:**

| 事件 | 说明 |
|------|------|
| `update` | 任务状态变化,data 为完整任务 JSON |
| `done` | 任务终态 (completed/failed/iteration_completed) |
| `error` | 错误 (任务不存在/超时) |

**示例:**

```javascript
const es = new EventSource(`/api/v1/tasks/${taskId}/stream?token=${token}`);
es.addEventListener("update", (e) => {
  const task = JSON.parse(e.data);
  console.log(task.status);
});
es.addEventListener("done", (e) => {
  es.close();
});
```

**超时:** 默认 300 秒,可通过 `QUANTGPT_SSE_TIMEOUT` 配置。

---

## 迭代优化

### POST /api/v1/tasks/{task_id}/iterate

基于已完成的回测任务,AI 自动生成多个改进候选因子。

**请求体:**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `n_candidates` | int | 否 | `5` | 候选数量 (1~10) |
| `run_rolling_validation` | bool | 否 | `false` | 是否运行滚动验证 |

**响应 202:**

```json
{
  "task_id": "iter_b2c3d4e5f6",
  "status": "pending"
}
```

**迭代完成后查询 (GET /tasks/{iter_task_id}):**

```json
{
  "task_id": "iter_b2c3d4e5f6",
  "status": "iteration_completed",
  "task_type": "iteration",
  "parent_task_id": "a1b2c3d4e5f6",
  "candidates_done": 5,
  "candidates_total": 5,
  "candidates": [
    {
      "expression": "rank(ts_corr(close, volume, 20)) * rank(ts_delta(close, 10)/close)",
      "status": "success",
      "score": 62.3,
      "grade": "B",
      "component_scores": { "sharpe": 55.0, "monotonicity": 80.0, "..." : "..." },
      "backtest_summary": { "...": "..." },
      "anti_overfit": { "score": 50.0, "mode": "fast", "...": "..." },
      "report_metrics": { "...": "..." },
      "report_url": "/api/v1/reports/backtest_report_xxx.html"
    },
    {
      "expression": "bad_expression",
      "status": "failed",
      "error": "表达式验证失败: Unknown column"
    }
  ],
  "result": {
    "parent_task_id": "a1b2c3d4e5f6",
    "parent_expression": "rank(close/ts_mean(close, 20))",
    "parent_score": 35.9,
    "parent_grade": "D",
    "candidates": [ "..." ]
  }
}
```

**SSE 支持:** 通过 `/tasks/{iter_task_id}/stream` 实时获取迭代进度,`candidates_done` 字段逐步递增。

---

### POST /api/v1/tasks/{task_id}/select_candidate

选择一个迭代候选因子。

**请求体:**

```json
{
  "candidate_index": 0
}
```

**响应 200:**

```json
{
  "task_id": "iter_b2c3d4e5f6",
  "selected_index": 0,
  "expression": "rank(ts_corr(close, volume, 20)) * rank(ts_delta(close, 10)/close)",
  "score": 62.3,
  "grade": "B",
  "report_url": "/api/v1/reports/backtest_report_xxx.html",
  "report_metrics": { "...": "..." },
  "backtest_summary": { "...": "..." }
}
```

---

## 报告

### GET /api/v1/reports/{filename}

下载 HTML 报告 (QuantStats 格式)。需认证,只能访问自己的报告。

```bash
curl -H "Authorization: Bearer <token>" \
  http://localhost:8003/api/v1/reports/backtest_report_20260321.html \
  -o report.html
```

**响应:** HTML 文件 (Content-Type: text/html)

---

## 反馈

### POST /api/v1/feedback

提交问题反馈,支持截图。

**请求体:**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `description` | string | 是 | 问题描述 (1~2000字) |
| `screenshot` | string | 否 | 截图 base64 (data:image/png;base64,...) 最大 5MB |
| `task_id` | string | 否 | 关联任务 ID |
| `page_url` | string | 否 | 当前页面 URL |
| `user_agent` | string | 否 | 浏览器 UA |

**响应 201:**

```json
{
  "id": "abc123def456",
  "status": "received",
  "webhook_sent": true
}
```

---

### GET /api/v1/feedback-screenshots/{feedback_id}

获取反馈截图 (PNG)。ID 不可猜测,无需认证。

---

## 管理后台

所有管理接口路径前缀: `/api/v1/admin`

### POST /api/v1/admin/login

管理员登录。

**请求体:**

```json
{
  "password": "admin_password"
}
```

**响应 200:**

```json
{
  "token": "admin_jwt_token"
}
```

---

### GET /api/v1/admin/overview

获取系统总览数据。

**响应 200:**

```json
{
  "total_users": 13,
  "total_tasks": 106,
  "success_rate": 0.81,
  "total_feedbacks": 5,
  "unresolved_feedbacks": 2,
  "status_distribution": { "completed": 72, "failed": 17, "iteration_completed": 17 },
  "daily_tasks_7d": [
    { "date": "2026-03-15", "count": 8 }
  ],
  "user_trend_30d": [
    { "date": "2026-03-01", "count": 2 }
  ]
}
```

---

### GET /api/v1/admin/users

用户列表 (分页)。

**查询参数:** `page`, `page_size`

---

### GET /api/v1/admin/tasks

任务列表 (分页,可按状态/用户过滤)。

**查询参数:** `page`, `page_size`, `status`, `user_id`

---

### GET /api/v1/admin/feedbacks

反馈列表 (分页)。

**查询参数:** `page`, `page_size`

---

### PATCH /api/v1/admin/feedbacks/{feedback_id}/resolve

标记反馈为已解决。

**响应 200:**

```json
{
  "id": "feedback_id",
  "resolved": true,
  "resolved_at": "2026-03-21T12:00:00"
}
```

---

## MCP Tools

QuantGPT 提供 MCP (Model Context Protocol) 服务,支持 8 个工具:

| Tool | 说明 |
|------|------|
| `list_operators` | 返回全部因子表达式算子及用法 |
| `list_universes` | 返回可用股票池和基准列表 |
| `validate_expression` | 验证表达式语法,返回 OK 或错误 |
| `run_backtest` | 执行回测,返回完整指标 + 反过拟合检测 + 报告路径 |
| `score_factor` | 执行回测并返回综合评分 (0-100, A/B/C/D) |
| `diagnose_factor` | 诊断因子问题,推荐突变策略 (6种) |
| `run_anti_overfit` | 独立反过拟合检测 (4项测试) |
| `run_rolling_validation` | Walk-Forward 滚动验证 |

### 配置 (.mcp.json)

```json
{
  "mcpServers": {
    "quantgpt": {
      "command": "python",
      "args": ["-m", "quantgpt"],
      "cwd": "/path/to/quantgpt"
    }
  }
}
```

---

## 健康检查

### GET /api/v1/health

无需认证。

**响应 200:**

```json
{
  "status": "ok",
  "active_tasks": 2,
  "total_tasks": 106
}
```

---

## 错误码

| HTTP 状态码 | 说明 |
|------------|------|
| 400 | 参数错误 (日期格式、表达式无效等) |
| 401 | 未认证或 Token 过期 |
| 403 | 权限不足 (管理接口) |
| 404 | 资源不存在 |
| 429 | 频率限制 (每分钟 10 次) |
| 503 | 服务繁忙 (并发任务已满) |

**错误响应格式:**

```json
{
  "detail": "错误描述信息"
}
```

---

## 股票池与基准

### 股票池

| 名称 | 说明 |
|------|------|
| `small_scale` | 5 只蓝筹 (茅台、平安、五粮液、美的、招行),快速测试 |
| `hs300` | 沪深300成分股,动态获取 |
| `csi500` | 中证500成分股,动态获取 |

### 基准指数

| 名称 | 说明 |
|------|------|
| `hs300` | 沪深300指数 |
| `zz500` | 中证500指数 |
| `sz50` | 上证50指数 |

---

## 因子表达式语法

### 支持的算子

**截面函数:** `rank(expr)`, `zscore(expr)`, `sign(expr)`, `log(expr)`, `abs(expr)`, `scale(expr)`

**时序函数:** `ts_mean(col,N)`, `ts_std(col,N)`, `ts_sum(col,N)`, `ts_max(col,N)`, `ts_min(col,N)`, `ts_shift(col,N)`, `ts_delta(col,N)`, `ts_rank(col,N)`, `ts_argmax(col,N)`, `ts_argmin(col,N)`, `decay_linear(col,N)`, `product(col,N)`

**双列时序:** `ts_corr(col1,col2,N)`, `ts_cov(col1,col2,N)`

**非线性:** `power(base,exp)`, `sign_power(base,exp)`, `tanh(expr)`, `sigmoid(expr)`, `exp(expr)`, `sqrt(expr)`

**条件:** `max(a,b)`, `min(a,b)`, `where(cond,true_val,false_val)`, `clip(expr,lower,upper)`

**算术:** `+`, `-`, `*`, `/`, `^`

**比较:** `>`, `<`, `>=`, `<=`, `==`, `!=`

**逻辑:** `and`, `or` (用于 where 条件)

**可用列:** `open`, `high`, `low`, `close`, `volume`, `amount`, `pct_change`

**特殊变量:** `vwap`, `returns`, `adv{N}` (如 adv20), `cap`

**别名:** `delta`=ts_delta, `delay`=ts_shift, `correlation`=ts_corr, `covariance`=ts_cov

### 示例

```
动量:     rank(close/ts_mean(close, 20))
反转:     rank(-1 * ts_delta(close, 5) / ts_shift(close, 5))
波动率:   rank(-1 * ts_std(returns, 20))
量价相关: rank(ts_corr(close, volume, 10))
复合:     sign_power(rank(volume/adv20), 2) * rank((close-vwap)/close)
```

---

## 典型调用流程

```
1. POST /auth/send-code          → 发送验证码
2. POST /auth/verify-code        → 获取 Token
3. POST /sessions                → 创建会话
4. POST /auto_backtest           → 提交回测
5. GET  /tasks/{id}/stream       → SSE 监听进度
6. GET  /tasks/{id}              → 获取完整结果
7. GET  /reports/{filename}      → 下载报告
8. POST /tasks/{id}/iterate      → AI 迭代优化
9. POST /tasks/{id}/select_candidate → 选择候选
```
