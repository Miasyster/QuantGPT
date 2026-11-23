import { useState } from "react";
import type { Task, IterationCandidate } from "../types/backtest";
import { getReportUrl } from "../api/client";
import { pct, num } from "../utils/format";
import { useColorMode } from "../contexts/ColorModeContext";

interface Props {
  parentTaskId: string;
  iterationTask: Task | null;
  isIterating: boolean;
  onIterate: (taskId: string, nCandidates?: number, direction?: string) => void;
  onSelectCandidate: (iterTaskId: string, index: number) => void;
}

function getGradeColors(isDark: boolean): Record<string, string> {
  return {
    A: isDark
      ? "bg-emerald-500/10 text-emerald-400"
      : "bg-emerald-100 text-emerald-700",
    B: isDark
      ? "bg-amber-500/10 text-amber-400"
      : "bg-blue-100 text-blue-700",
    C: isDark
      ? "bg-amber-500/10 text-amber-400"
      : "bg-amber-100 text-amber-700",
    D: isDark
      ? "bg-red-500/10 text-red-400"
      : "bg-red-100 text-red-700",
  };
}

function CandidateRow({
  candidate,
  index,
  isSelected,
  isBest,
  onSelect,
}: {
  candidate: IterationCandidate;
  index: number;
  isSelected: boolean;
  isBest: boolean;
  onSelect: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const { isDark } = useColorMode();
  const GRADE_COLORS = getGradeColors(isDark);

  if (candidate.status === "failed") {
    return (
      <tr className="text-gray-400">
        <td className="px-3 py-2 text-center">{index + 1}</td>
        <td className="px-3 py-2 font-mono text-xs truncate max-w-[200px]">
          {candidate.expression}
        </td>
        <td className="px-3 py-2 text-center">-</td>
        <td className="px-3 py-2 text-center">-</td>
        <td className="px-3 py-2 text-center">-</td>
        <td className="px-3 py-2 text-center">-</td>
        <td className="px-3 py-2 text-center text-red-400 text-xs">{candidate.error}</td>
      </tr>
    );
  }

  return (
    <>
      <tr
        className={`cursor-pointer transition-colors ${isBest ? (isDark ? "bg-emerald-500/10" : "bg-emerald-50") : (isDark ? "hover:bg-gray-800" : "hover:bg-gray-50")} ${isSelected ? "ring-2 ring-inset ring-blue-400" : ""}`}
        onClick={() => setExpanded(!expanded)}
      >
        <td className="px-3 py-2 text-center text-sm">
          {isBest && <span className="mr-1">&#9733;</span>}
          {index + 1}
        </td>
        <td className="px-3 py-2 font-mono text-xs max-w-[200px]">
          <div className="truncate" title={candidate.expression}>{candidate.expression}</div>
        </td>
        <td className="px-3 py-2 text-center text-sm font-medium">{candidate.score.toFixed(1)}</td>
        <td className="px-3 py-2 text-center">
          <span className="inline-flex items-center gap-1">
            <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${GRADE_COLORS[candidate.grade] || ""}`}>
              {candidate.grade}
            </span>
          </span>
        </td>
        <td className="px-3 py-2 text-center text-sm">{num(candidate.report_metrics.sharpe)}</td>
        <td className="px-3 py-2 text-center text-sm">{num(candidate.backtest_summary.monotonicity_score)}</td>
        <td className="px-3 py-2 text-center">
          <button
            onClick={(e) => { e.stopPropagation(); onSelect(); }}
            className={`text-xs px-3 py-1 rounded-md font-medium transition-colors ${
              isSelected
                ? "bg-blue-600 text-white"
                : isDark
                  ? "bg-gray-800 text-gray-300 hover:bg-amber-500/10 hover:text-amber-400"
                  : "bg-gray-100 text-gray-700 hover:bg-blue-50 hover:text-blue-700"
            }`}
          >
            {isSelected ? "已选择" : "选择此因子"}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className={isDark ? "bg-gray-800" : "bg-gray-50"}>
          <td colSpan={7} className="px-4 py-3">
            <div className="grid grid-cols-4 gap-3 text-xs">
              <div>
                <span className={isDark ? "text-gray-400" : "text-gray-500"}>Sharpe:</span>{" "}
                <span className="font-medium">{num(candidate.report_metrics.sharpe)}</span>
              </div>
              <div>
                <span className={isDark ? "text-gray-400" : "text-gray-500"}>CAGR:</span>{" "}
                <span className="font-medium">{pct(candidate.report_metrics.cagr)}</span>
              </div>
              <div>
                <span className={isDark ? "text-gray-400" : "text-gray-500"}>最大回撤:</span>{" "}
                <span className={`font-medium ${isDark ? "text-red-400" : "text-red-600"}`}>{pct(candidate.report_metrics.max_drawdown)}</span>
              </div>
              <div>
                <span className={isDark ? "text-gray-400" : "text-gray-500"}>胜率:</span>{" "}
                <span className="font-medium">{pct(candidate.report_metrics.win_rate)}</span>
              </div>
              <div>
                <span className={isDark ? "text-gray-400" : "text-gray-500"}>价差:</span>{" "}
                <span className="font-medium">{pct(candidate.backtest_summary.spread)}</span>
              </div>
              <div>
                <span className={isDark ? "text-gray-400" : "text-gray-500"}>波动率:</span>{" "}
                <span className="font-medium">{pct(candidate.report_metrics.volatility)}</span>
              </div>
              <div>
                <span className={isDark ? "text-gray-400" : "text-gray-500"}>Sortino:</span>{" "}
                <span className="font-medium">{num(candidate.report_metrics.sortino)}</span>
              </div>
              <div>
                <a
                  href={getReportUrl(candidate.report_url)}
                  target="_blank"
                  rel="noreferrer"
                  className={isDark ? "text-amber-400 hover:underline" : "text-blue-600 hover:underline"}
                >
                  查看报告
                </a>
              </div>
            </div>
            <div className={`mt-2 text-xs font-mono break-all ${isDark ? "text-gray-400" : "text-gray-500"}`}>
              {candidate.expression}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export default function IterationPanel({
  parentTaskId,
  iterationTask,
  isIterating,
  onIterate,
  onSelectCandidate,
}: Props) {
  const [direction, setDirection] = useState("");
  const [showDirectionInput, setShowDirectionInput] = useState(false);
  const { isDark } = useColorMode();
  const GRADE_COLORS = getGradeColors(isDark);

  const DIRECTION_PRESETS = [
    "加入量价信息",
    "增加低波暴露",
    "融合动量与反转",
    "加入非线性变换",
    "增强行业中性",
  ];

  // Trigger state — no iteration started
  if (!iterationTask && !isIterating) {
    return (
      <div className={`rounded-xl border border-dashed ${isDark ? "border-gray-700 bg-gray-900" : "border-gray-300 bg-white"} p-5`}>
        <p className={`text-sm mb-3 text-center ${isDark ? "text-gray-400" : "text-gray-500"}`}>对当前结果不满意？尝试让 AI 自动优化因子</p>

        {showDirectionInput && (
          <div className="mb-3 space-y-2">
            <div className="flex flex-wrap gap-1.5">
              {DIRECTION_PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  onClick={() => setDirection(preset)}
                  className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                    direction === preset
                      ? isDark
                        ? "bg-amber-500/10 border-amber-500 text-amber-400"
                        : "bg-blue-50 border-blue-300 text-blue-700"
                      : isDark
                        ? "border-gray-700 text-gray-400 hover:border-amber-500 hover:text-amber-400"
                        : "border-gray-200 text-gray-500 hover:border-blue-200 hover:text-blue-600"
                  }`}
                >
                  {preset}
                </button>
              ))}
            </div>
            <input
              type="text"
              value={direction}
              onChange={(e) => setDirection(e.target.value)}
              placeholder="输入自定义迭代方向，如：加入量价信息、增加低波暴露..."
              className={`w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
                isDark
                  ? "border-gray-700 bg-gray-800 text-gray-100 focus:ring-amber-500/20 focus:border-amber-500"
                  : "border-gray-200 focus:ring-blue-500/20 focus:border-blue-500"
              }`}
            />
          </div>
        )}

        <div className="flex items-center justify-center gap-2">
          <button
            onClick={() => setShowDirectionInput(!showDirectionInput)}
            className={`text-xs transition-colors ${isDark ? "text-gray-400 hover:text-amber-400" : "text-gray-400 hover:text-blue-600"}`}
          >
            {showDirectionInput ? "收起方向设置" : "指定迭代方向"}
          </button>
          <button
            onClick={() => onIterate(parentTaskId, 5, direction || undefined)}
            className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-blue-600 to-indigo-600 px-5 py-2 text-sm font-medium text-white shadow-sm hover:from-blue-700 hover:to-indigo-700 transition-all"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            {direction ? "按方向迭代" : "迭代优化 (5 个候选)"}
          </button>
        </div>
      </div>
    );
  }

  // Error state
  if (iterationTask?.status === "failed") {
    return (
      <div className={`rounded-xl border ${isDark ? "border-red-500/30 bg-red-500/10" : "border-red-200 bg-red-50"} p-4`}>
        <p className={`text-sm font-medium ${isDark ? "text-red-400" : "text-red-700"}`}>迭代优化失败</p>
        <p className={`mt-1 text-sm ${isDark ? "text-red-400" : "text-red-600"}`}>{iterationTask.error}</p>
        <button
          onClick={() => onIterate(parentTaskId)}
          className={`mt-3 text-xs underline ${isDark ? "text-red-400 hover:text-red-300" : "text-red-600 hover:text-red-800"}`}
        >
          重试
        </button>
        <p className="mt-2 text-xs text-red-400">如果问题持续出现，欢迎点击右下角「反馈」按钮告诉我们，我们会尽快修复。</p>
      </div>
    );
  }

  // In-progress state
  if (isIterating || iterationTask?.status === "iterating") {
    const done = iterationTask?.candidates_done ?? 0;
    const total = iterationTask?.candidates_total ?? 5;
    const progressPct = total > 0 ? (done / total) * 100 : 0;

    return (
      <div className={`rounded-xl border ${isDark ? "border-amber-500/30 bg-amber-500/10" : "border-blue-200 bg-blue-50"} p-5`}>
        <div className="flex items-center justify-between mb-3">
          <p className={`text-sm font-medium ${isDark ? "text-amber-400" : "text-blue-800"}`}>迭代优化进行中...</p>
          <span className={`text-xs ${isDark ? "text-amber-400" : "text-blue-600"}`}>{done} / {total} 候选完成</span>
        </div>
        <div className={`w-full rounded-full h-2 ${isDark ? "bg-amber-500/20" : "bg-blue-200"}`}>
          <div
            className={`h-2 rounded-full transition-all duration-500 ${isDark ? "bg-amber-500" : "bg-blue-600"}`}
            style={{ width: `${progressPct}%` }}
          />
        </div>
        {iterationTask?.candidates && iterationTask.candidates.length > 0 && (
          <div className="mt-3 space-y-1">
            {iterationTask.candidates
              .filter((c) => c.status === "success")
              .map((c, i) => (
                <div key={i} className={`flex items-center gap-2 text-xs ${isDark ? "text-amber-400" : "text-blue-700"}`}>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${GRADE_COLORS[c.grade]}`}>
                    {c.grade}
                  </span>
                  <span className="font-mono truncate">{c.expression}</span>
                  <span className="ml-auto font-medium">{c.score.toFixed(1)}分</span>
                </div>
              ))}
          </div>
        )}
      </div>
    );
  }

  // Completed state — show comparison table
  if (iterationTask?.status === "iteration_completed") {
    const candidates = (iterationTask.candidates ?? []).filter(
      (c): c is IterationCandidate => c.status === "success" || c.status === "failed"
    );
    const selectedIndex = iterationTask.selected_candidate_index;
    const bestIndex = candidates.findIndex((c) => c.status === "success");

    if (candidates.length === 0) {
      return (
        <div className={`rounded-xl border ${isDark ? "border-amber-500/30 bg-amber-500/10" : "border-amber-200 bg-amber-50"} p-4`}>
          <p className={`text-sm ${isDark ? "text-amber-400" : "text-amber-700"}`}>所有候选因子生成失败，请重试</p>
          <button
            onClick={() => onIterate(parentTaskId)}
            className={`mt-2 text-xs underline ${isDark ? "text-amber-400 hover:text-amber-300" : "text-amber-600 hover:text-amber-800"}`}
          >
            重新迭代
          </button>
        </div>
      );
    }

    return (
      <div className={`rounded-xl border ${isDark ? "border-gray-700 bg-gray-900" : "border-gray-200 bg-white"} overflow-hidden`}>
        <div className={`flex items-center justify-between px-4 py-3 border-b ${isDark ? "border-gray-700" : "border-gray-100"}`}>
          <h3 className={`text-sm font-medium ${isDark ? "text-gray-300" : "text-gray-700"}`}>迭代优化结果</h3>
          <button
            onClick={() => onIterate(parentTaskId)}
            className={`text-xs ${isDark ? "text-amber-400 hover:text-amber-300" : "text-blue-600 hover:text-blue-800"}`}
          >
            再次迭代
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className={`border-b ${isDark ? "border-gray-700 text-gray-400" : "border-gray-100 text-gray-500"}`}>
                <th className="px-3 py-2 text-center w-12">#</th>
                <th className="px-3 py-2 text-left">表达式</th>
                <th className="px-3 py-2 text-center w-16">评分</th>
                <th className="px-3 py-2 text-center w-16">等级</th>
                <th className="px-3 py-2 text-center w-20">Sharpe</th>
                <th className="px-3 py-2 text-center w-20">单调性</th>
                <th className="px-3 py-2 text-center w-28">操作</th>
              </tr>
            </thead>
            <tbody className={`divide-y ${isDark ? "divide-gray-800" : "divide-gray-50"}`}>
              {candidates.map((c, i) => (
                <CandidateRow
                  key={i}
                  candidate={c}
                  index={i}
                  isSelected={selectedIndex === i}
                  isBest={i === bestIndex}
                  onSelect={() => onSelectCandidate(iterationTask.task_id, i)}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return null;
}
