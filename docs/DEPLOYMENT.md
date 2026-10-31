# 部署指南

将 QuantGPT 部署到你自己的服务器上运行。

---

## 1. 环境要求

| 项目 | 最低配置 | 推荐配置 |
|:-----|:---------|:---------|
| CPU | 2 核 | 4 核 |
| 内存 | 4 GB | 8 GB |
| 磁盘 | 20 GB | 50 GB（含行情缓存） |
| OS | Ubuntu 22.04 / Debian 12 / macOS | 同左 |
| Python | 3.10+ | 3.12 |
| Docker | 24.0+（Docker 部署方式） | 同左 |

---

## 2. 部署方式

### 方式一：Docker 部署（推荐）

```bash
git clone https://github.com/Miasyster/QuantGPT.git
cd QuantGPT

# 准备配置
cp .env.example .env
# 编辑 .env（见第 3 节）

# 构建并启动
docker compose up -d --build

# 查看日志
docker compose logs -f
```

服务启动后访问 `http://localhost:8003`。

### 方式二：裸机部署

```bash
git clone https://github.com/Miasyster/QuantGPT.git
cd QuantGPT

python3 -m venv venv
source venv/bin/activate
pip install -e .

# 构建前端
cd frontend && npm ci && npm run build && cd ..

cp .env.example .env
bash restart.sh
```

---

## 3. 配置说明

编辑 `.env` 文件：

### 3.1 LLM（可选）

```bash
# 不填则为纯表达式模式（手动输入因子表达式，无 AI 生成）
LLM_API_KEY=sk-你的key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

可换成任何兼容 OpenAI 接口的模型。旧变量 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 仍然有效，同时设置时优先使用 `LLM_*`。

### 3.2 数据库

```bash
# 默认 SQLite，零配置，留空即可
# 如需 PostgreSQL：
# DATABASE_URL=postgresql+asyncpg://quantgpt:password@localhost:5433/quantgpt
```

Docker 部署默认把 SQLite 存放在 `quantgpt-data` 卷（`/app/data/quantgpt.db`），不再挂载项目根目录的 `quantgpt.db`。升级时如需保留旧数据，请把旧文件复制到该卷中。

### 3.3 认证

```bash
# 本地使用建议保持 true，免登录
AUTH_DISABLED=true

# 如需多用户登录，设为 false 并配置以下项：
# JWT_SECRET_KEY=（openssl rand -hex 32 生成）
# SMTP_HOST=smtp.example.com
# SMTP_PORT=465
# SMTP_USER=your_email
# SMTP_PASSWORD=your_password
# SMTP_FROM=your_email
# SMTP_USE_TLS=true
```

---

## 4. MCP 集成

部署完成后，可通过 MCP 协议连接 Claude Code / Claude Desktop：

```json
{
  "mcpServers": {
    "quantgpt": {
      "type": "stdio",
      "command": "python3",
      "args": ["-m", "quantgpt"],
      "cwd": "/你的项目路径/QuantGPT"
    }
  }
}
```

配置文件位置：
- Claude Code：项目根目录 `.mcp.json`
- Claude Desktop (Mac)：`~/Library/Application Support/Claude/claude_desktop_config.json`
- Claude Desktop (Win)：`%APPDATA%\Claude\claude_desktop_config.json`

---

## 5. 数据预热（可选）

首次回测会自动下载行情数据，也可以提前预热：

```bash
python -m quantgpt --prefetch hs300 csi500
```

---

## 6. 常见问题

### Windows 中文系统启动报 UnicodeDecodeError

确保使用最新版代码（已修复）。如果仍有问题，将 `.env` 文件用记事本另存为 UTF-8 编码。

### Docker 中连接不上宿主机 PostgreSQL

使用 `host.docker.internal` 替代 `localhost`：

```bash
DATABASE_URL=postgresql+asyncpg://quantgpt:password@host.docker.internal:5433/quantgpt
```

### 前端页面空白

确认已构建前端：`cd frontend && npm ci && npm run build`。Docker 方式会自动构建。

### MCP 连接失败

1. 确认 `cwd` 是绝对路径
2. 在项目目录下手动运行 `python3 -m quantgpt` 检查是否有报错
3. 确认 `.env` 文件编码正确且无语法错误
