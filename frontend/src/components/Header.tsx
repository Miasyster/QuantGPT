import { useState } from "react";
import { BarChart3, X, Terminal, Copy, Check, ExternalLink, Sun, Moon } from "lucide-react";
import { useColorMode } from "../contexts/ColorModeContext";

export const APP_VERSION = "v2.9.0";

const CHANGELOG = [
  {
    version: "v2.9.0",
    date: "2026-10-31",
    items: [
      "新增 LLM_* 配置项，可接入任意 OpenAI 兼容模型",
      "修复 baostock 输出污染 MCP stdio 通道",
      "修复 LLM 返回空内容时解读和日报报错",
      "优化 Codex 接入文档，统一使用 /mcp 入口",
    ],
  },
  {
    version: "v2.8.1",
    date: "2026-10-31",
    items: [
      "修复干净环境下 Docker 部署无法启动",
      "修复 DATABASE_URL 留空时启动报错",
      "修复 restart.sh 未使用虚拟环境",
      "修复基本面数据日期精度不兼容报错",
      "优化 make setup 的 Python 版本检查",
      "优化 WQ BRAIN 专属字段的本地报错提示",
    ],
  },
  {
    version: "v2.8.0",
    date: "2026-04-30",
    items: [
      "WQ BRAIN 提交修复：SC 轮询确认机制，提交状态与平台 ACTIVE 严格对齐",
      "新增 alpha-status / submit-by-id 端点，支持单个 alpha 状态查询和直接提交",
      "累计 8 个因子平台 ACTIVE（ts_decay_linear 3 + ts_av_diff 5）",
    ],
  },
  {
    version: "v2.7.0",
    date: "2026-04-29",
    items: [
      "WQ BRAIN 自主因子挖掘：3 轮 Session、8 轮实验，产出 21 个 A 级因子",
      "发现 ts_av_diff 第二独立算子家族，突破 SC 饱和瓶颈",
      "新增批量提交 + 并发控制 + 连接异常自动重试",
    ],
  },
  {
    version: "v2.6.0",
    date: "2026-04-29",
    items: [
      "移除 SC 本地检查，A 级因子直接提交平台判定",
      "WQ 模式放宽校验：未知算子/字段透传 BRAIN 不再本地拦截",
      "ResearchDashboard 金融量化风格重构",
    ],
  },
];

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const { isDark } = useColorMode();
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
      className={`absolute top-2 right-2 p-1 rounded text-gray-400 ${isDark ? "hover:text-gray-300 hover:bg-gray-800" : "hover:text-gray-600 hover:bg-gray-100"} transition-colors`}
      title="复制"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

function McpGuideModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<"claude" | "openclaw">("claude");
  const { isDark } = useColorMode();

  const mcpConfig = `{
  "mcpServers": {
    "quantgpt": {
      "type": "stdio",
      "command": "python3",
      "args": ["-m", "quantgpt"],
      "env": {
        "PYTHONPATH": "/path/to/QuantGPT"
      }
    }
  }
}`;

  const openclawNativeCode = `from openclaw.tools.mcp import MCPClient

client = MCPClient(
    server_url="http://localhost:8002/mcp"
)

agent = Agent(
    tools=client.get_tools()
)`;

  const openclawManualCode = `import requests

def backtest_tool(expression: str, **kwargs):
    return requests.post(
        "http://localhost:8002/mcp",
        json={"expression": expression, **kwargs}
    ).json()

agent.register_tool(backtest_tool)`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className={`${isDark ? "bg-gray-900" : "bg-white"} rounded-2xl shadow-xl w-full max-w-lg mx-4 max-h-[85vh] flex flex-col`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={`flex items-center justify-between px-5 py-4 border-b ${isDark ? "border-gray-700" : "border-gray-100"}`}>
          <div className="flex items-center gap-2.5">
            <div className="h-8 w-8 rounded-lg bg-gradient-to-br from-orange-500 to-amber-500 flex items-center justify-center">
              <Terminal className="h-4 w-4 text-white" />
            </div>
            <div>
              <h2 className={`text-base font-semibold ${isDark ? "text-gray-100" : "text-gray-900"}`}>MCP 集成指南</h2>
              <p className="text-xs text-gray-400">通过 MCP 协议接入 QuantGPT 回测能力</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className={`p-1.5 rounded-lg text-gray-400 ${isDark ? "hover:text-gray-300 hover:bg-gray-800" : "hover:text-gray-600 hover:bg-gray-100"} transition-colors`}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className={`px-5 pt-3 flex gap-1 border-b ${isDark ? "border-gray-700" : "border-gray-100"}`}>
          <button
            onClick={() => setTab("claude")}
            className={`px-3 py-2 text-sm font-medium rounded-t-lg transition-colors ${
              tab === "claude"
                ? isDark
                  ? "text-orange-400 border-b-2 border-orange-500 bg-orange-500/10"
                  : "text-orange-600 border-b-2 border-orange-500 bg-orange-50/50"
                : isDark
                  ? "text-gray-400 hover:text-gray-300 hover:bg-gray-800"
                  : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
            }`}
          >
            Claude Code
          </button>
          <button
            onClick={() => setTab("openclaw")}
            className={`px-3 py-2 text-sm font-medium rounded-t-lg transition-colors ${
              tab === "openclaw"
                ? isDark
                  ? "text-amber-400 border-b-2 border-amber-500 bg-amber-500/10"
                  : "text-blue-600 border-b-2 border-blue-500 bg-blue-50/50"
                : isDark
                  ? "text-gray-400 hover:text-gray-300 hover:bg-gray-800"
                  : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
            }`}
          >
            OpenClaw / Agent
          </button>
        </div>

        <div className="overflow-y-auto px-5 py-4 space-y-5">
          {tab === "claude" ? (
            <>
              {/* What is MCP */}
              <div className={`${isDark ? "bg-amber-500/10" : "bg-blue-50"} rounded-lg p-3.5`}>
                <p className={`text-sm ${isDark ? "text-amber-300" : "text-blue-800"}`}>
                  <span className="font-medium">什么是 MCP？</span>{" "}
                  MCP (Model Context Protocol) 让 Claude 直接调用 QuantGPT 的回测工具。
                  配置后，Claude 可以直接调用回测、评分、诊断等研究工具。
                </p>
              </div>

              {/* Step 1: Clone */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className={`h-5 w-5 rounded-full ${isDark ? "bg-gray-100" : "bg-gray-900"} ${isDark ? "text-gray-900" : "text-white"} text-xs flex items-center justify-center font-medium`}>1</span>
                  <h3 className={`text-sm font-medium ${isDark ? "text-gray-100" : "text-gray-900"}`}>克隆项目并安装</h3>
                </div>
                <div className="relative bg-gray-900 rounded-lg p-3 font-mono text-xs text-gray-100 leading-relaxed">
                  <CopyButton text={"git clone https://github.com/Miasyster/QuantGPT.git\ncd QuantGPT\npip install -e ."} />
                  <pre className="whitespace-pre-wrap"><span className="text-green-400">$</span> git clone https://github.com/Miasyster/QuantGPT.git{"\n"}<span className="text-green-400">$</span> cd QuantGPT{"\n"}<span className="text-green-400">$</span> pip install -e .</pre>
                </div>
              </div>

              {/* Step 2: Configure */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className={`h-5 w-5 rounded-full ${isDark ? "bg-gray-100" : "bg-gray-900"} ${isDark ? "text-gray-900" : "text-white"} text-xs flex items-center justify-center font-medium`}>2</span>
                  <h3 className={`text-sm font-medium ${isDark ? "text-gray-100" : "text-gray-900"}`}>配置 MCP</h3>
                </div>
                <p className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"} mb-1.5 font-medium`}>在项目根目录创建 <code className={`${isDark ? "bg-gray-800" : "bg-gray-100"} px-1 rounded`}>.mcp.json</code>：</p>
                <div className="relative bg-gray-900 rounded-lg p-3 font-mono text-xs text-gray-100 leading-relaxed">
                  <CopyButton text={mcpConfig} />
                  <pre className="whitespace-pre-wrap">{mcpConfig}</pre>
                </div>
                <p className="text-xs text-gray-400 mt-1.5">
                  将 <code className={`${isDark ? "bg-gray-800 text-gray-300" : "bg-gray-100 text-gray-600"} px-1 rounded`}>PYTHONPATH</code> 替换为实际项目路径。需配置米筐数据源，详见项目 README。
                </p>
              </div>

              {/* Step 3 */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className={`h-5 w-5 rounded-full ${isDark ? "bg-gray-100" : "bg-gray-900"} ${isDark ? "text-gray-900" : "text-white"} text-xs flex items-center justify-center font-medium`}>3</span>
                  <h3 className={`text-sm font-medium ${isDark ? "text-gray-100" : "text-gray-900"}`}>开始使用</h3>
                </div>
                <div className="relative bg-gray-900 rounded-lg p-3 font-mono text-sm text-gray-100">
                  <CopyButton text="claude mcp list" />
                  <div>
                    <span className="text-green-400">$</span> claude mcp list<br/>
                    <span className="text-gray-400"># quantgpt: Connected</span>
                  </div>
                </div>
                <p className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"} mt-2`}>
                  验证连接后，Agent 可自主调用工具进行因子研究：回测、评分、诊断、反过拟合检测
                </p>
              </div>
            </>
          ) : (
            <>
              {/* OpenClaw intro */}
              <div className={`${isDark ? "bg-amber-500/10" : "bg-blue-50"} rounded-lg p-3.5`}>
                <p className={`text-sm ${isDark ? "text-amber-300" : "text-blue-800"}`}>
                  <span className="font-medium">架构说明</span>{" "}
                  OpenClaw 是 Agent 调度框架，通过 MCP 协议动态调用 QuantGPT 的回测能力。
                  QuantGPT 作为 MCP Server 暴露标准化工具接口。
                </p>
              </div>

              {/* Architecture diagram */}
              <div className={`${isDark ? "bg-gray-800" : "bg-gray-50"} rounded-lg p-3`}>
                <pre className={`text-xs ${isDark ? "text-gray-400" : "text-gray-600"} leading-relaxed font-mono`}>{`[OpenClaw Agent]
      ↓
[MCP Client]
      ↓  Streamable HTTP
[QuantGPT MCP Server (localhost:8002)]
      ↓
[回测 / 评分 / 诊断 / 验证]`}</pre>
              </div>

              {/* Step 1: Connect */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className={`h-5 w-5 rounded-full ${isDark ? "bg-gray-100" : "bg-gray-900"} ${isDark ? "text-gray-900" : "text-white"} text-xs flex items-center justify-center font-medium`}>1</span>
                  <h3 className={`text-sm font-medium ${isDark ? "text-gray-100" : "text-gray-900"}`}>在 Agent 中接入</h3>
                </div>
                <p className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"} mb-1.5`}>MCP 端点地址：<code className={`${isDark ? "bg-gray-800" : "bg-gray-100"} px-1 rounded`}>http://localhost:8002/mcp</code></p>
                <div className="space-y-3">
                  <div>
                    <p className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"} mb-1.5 font-medium`}>方式 A：原生 MCP Client（推荐）</p>
                    <div className="relative bg-gray-900 rounded-lg p-3 font-mono text-xs text-gray-100 leading-relaxed">
                      <CopyButton text={openclawNativeCode} />
                      <pre className="whitespace-pre-wrap">{openclawNativeCode}</pre>
                    </div>
                  </div>
                  <div>
                    <p className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"} mb-1.5 font-medium`}>方式 B：手动封装（兼容旧版本）</p>
                    <div className="relative bg-gray-900 rounded-lg p-3 font-mono text-xs text-gray-100 leading-relaxed">
                      <CopyButton text={openclawManualCode} />
                      <pre className="whitespace-pre-wrap">{openclawManualCode}</pre>
                    </div>
                  </div>
                </div>
              </div>

              {/* Tips */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className={`h-5 w-5 rounded-full ${isDark ? "bg-gray-100" : "bg-gray-900"} ${isDark ? "text-gray-900" : "text-white"} text-xs flex items-center justify-center font-medium`}>2</span>
                  <h3 className={`text-sm font-medium ${isDark ? "text-gray-100" : "text-gray-900"}`}>注意事项</h3>
                </div>
                <div className="space-y-1.5">
                  {[
                    "Tool 描述必须清晰，LLM 依赖描述决定是否调用",
                    "输入参数要结构化（expression, start_date, end_date）",
                    "返回值保持精简，避免大 JSON",
                    "工具数量建议 ≤ 10 个，过多会导致调用混乱",
                  ].map((tip, i) => (
                    <div key={i} className={`flex items-start gap-2 text-xs ${isDark ? "text-gray-400" : "text-gray-600"}`}>
                      <span className="mt-0.5 h-1.5 w-1.5 rounded-full bg-amber-400 shrink-0" />
                      {tip}
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* Available tools - shared */}
          <div>
            <h3 className={`text-sm font-medium ${isDark ? "text-gray-100" : "text-gray-900"} mb-2`}>可用工具（8 个）</h3>
            <div className="grid grid-cols-2 gap-1.5">
              {[
                { name: "list_operators", desc: "查看算子列表" },
                { name: "list_universes", desc: "查看股票池" },
                { name: "validate_expression", desc: "验证表达式" },
                { name: "run_backtest", desc: "执行回测" },
                { name: "score_factor", desc: "因子评分" },
                { name: "diagnose_factor", desc: "诊断因子" },
                { name: "run_anti_overfit", desc: "抗过拟合检测" },
                { name: "run_rolling_validation", desc: "滚动验证" },
              ].map((tool) => (
                <div key={tool.name} className={`flex items-center gap-2 px-2.5 py-1.5 rounded-md ${isDark ? "bg-gray-800" : "bg-gray-50"}`}>
                  <code className="text-xs text-orange-600 font-medium">{tool.name}</code>
                  <span className="text-xs text-gray-400">{tool.desc}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className={`px-5 py-3 border-t ${isDark ? "border-gray-700" : "border-gray-100"} flex items-center justify-between`}>
          <a
            href="https://github.com/Miasyster/QuantGPT"
            target="_blank"
            rel="noopener noreferrer"
            className={`flex items-center gap-1.5 text-sm ${isDark ? "text-gray-400 hover:text-gray-300" : "text-gray-500 hover:text-gray-700"} transition-colors`}
          >
            <ExternalLink className="h-3.5 w-3.5" />
            GitHub
          </a>
          <button
            onClick={onClose}
            className={`px-4 py-1.5 rounded-lg text-sm font-medium ${isDark ? "text-gray-400 hover:bg-gray-800" : "text-gray-600 hover:bg-gray-100"} transition-colors`}
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}

function ColorModeToggle() {
  const { colorMode, toggleColorMode, isDark } = useColorMode();
  return (
    <button
      onClick={toggleColorMode}
      className={`flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium border ${isDark ? "border-gray-700 hover:bg-gray-800" : "border-gray-200 hover:bg-gray-50"} transition-colors`}
      title={colorMode === "cn" ? "当前：红涨绿跌（中国）" : "当前：绿涨红跌（西方）"}
    >
      <span className={colorMode === "cn" ? "text-red-500" : "text-emerald-500"}>涨</span>
      <span className="text-gray-300">/</span>
      <span className={colorMode === "cn" ? "text-emerald-500" : "text-red-500"}>跌</span>
    </button>
  );
}

function DarkModeToggle() {
  const { isDark, toggleDark } = useColorMode();
  return (
    <button
      onClick={toggleDark}
      className={`p-1.5 rounded-md border ${isDark ? "border-gray-700 hover:bg-gray-800" : "border-gray-200 hover:bg-gray-50"} transition-colors`}
      title={isDark ? "切换到浅色模式" : "切换到深色模式"}
    >
      {isDark ? <Sun className="h-3.5 w-3.5 text-amber-400" /> : <Moon className="h-3.5 w-3.5 text-gray-500" />}
    </button>
  );
}

export default function Header() {
  const { isDark } = useColorMode();
  const [showChangelog, setShowChangelog] = useState(false);
  const [showMcpGuide, setShowMcpGuide] = useState(false);

  return (
    <>
      <header className={`border-b ${isDark ? "border-gray-700 bg-gray-900" : "border-gray-200 bg-white"}`}>
        <div className="mx-auto max-w-7xl px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <BarChart3 className={`h-6 w-6 ${isDark ? "text-amber-400" : "text-blue-600"}`} />
            <div>
              <div className="flex items-center gap-2">
                <h1 className={`text-lg font-semibold ${isDark ? "text-gray-100" : "text-gray-900"}`}>QuantGPT</h1>
                <button
                  onClick={() => setShowChangelog(true)}
                  className={`text-xs px-1.5 py-0.5 rounded ${isDark ? "bg-amber-500/10 text-amber-400 hover:bg-amber-500/20" : "bg-blue-50 text-blue-600 hover:bg-blue-100"} transition-colors font-mono`}
                >
                  {APP_VERSION}
                </button>
              </div>
              <p className={`text-sm ${isDark ? "text-gray-400" : "text-gray-500"}`}>Agent 驱动的 LLM 量化研究引擎</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <DarkModeToggle />
            <ColorModeToggle />
            <button
              onClick={() => setShowMcpGuide(true)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm text-orange-600 hover:bg-orange-50 transition-colors"
              title="MCP 集成指南"
            >
              <Terminal className="h-4 w-4" />
              <span className="hidden sm:inline">MCP</span>
            </button>
          </div>
        </div>
      </header>

      {showChangelog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setShowChangelog(false)}
        >
          <div
            className={`${isDark ? "bg-gray-900" : "bg-white"} rounded-2xl shadow-xl w-full max-w-md mx-4 max-h-[80vh] flex flex-col`}
            onClick={(e) => e.stopPropagation()}
          >
            <div className={`flex items-center justify-between px-5 py-4 border-b ${isDark ? "border-gray-700" : "border-gray-100"}`}>
              <div>
                <h2 className={`text-base font-semibold ${isDark ? "text-gray-100" : "text-gray-900"}`}>更新日志</h2>
                <p className="text-xs text-gray-400 mt-0.5">当前版本 {APP_VERSION}</p>
              </div>
              <button
                onClick={() => setShowChangelog(false)}
                className={`p-1.5 rounded-lg text-gray-400 ${isDark ? "hover:text-gray-300 hover:bg-gray-800" : "hover:text-gray-600 hover:bg-gray-100"} transition-colors`}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="overflow-y-auto px-5 py-4 space-y-5">
              {CHANGELOG.map((release) => (
                <div key={release.version}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className={`text-sm font-semibold ${isDark ? "text-gray-100" : "text-gray-900"} font-mono`}>{release.version}</span>
                    <span className="text-xs text-gray-400">{release.date}</span>
                    {release.version === APP_VERSION && (
                      <span className={`text-xs px-1.5 py-0.5 rounded ${isDark ? "bg-amber-500/10 text-amber-400" : "bg-blue-50 text-blue-600"}`}>当前</span>
                    )}
                  </div>
                  <ul className="space-y-1">
                    {release.items.map((item, i) => (
                      <li key={i} className={`flex items-start gap-2 text-sm ${isDark ? "text-gray-400" : "text-gray-600"}`}>
                        <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-gray-300 shrink-0" />
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {showMcpGuide && <McpGuideModal onClose={() => setShowMcpGuide(false)} />}
    </>
  );
}
