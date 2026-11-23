import type { RobustnessResult, ScoringResult } from "../types/backtest";
import { useColorMode } from "../contexts/ColorModeContext";
import { Shield, ShieldCheck, ShieldAlert, ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";

interface Props {
  antiOverfit?: RobustnessResult | null;
  adversarial?: RobustnessResult | null;
  scoring?: ScoringResult | null;
}

const GRADE_COLORS_DARK: Record<string, string> = {
  A: "text-emerald-400",
  B: "text-blue-400",
  C: "text-amber-400",
  D: "text-red-400",
};

const GRADE_COLORS_LIGHT: Record<string, string> = {
  A: "text-emerald-600",
  B: "text-blue-600",
  C: "text-amber-600",
  D: "text-red-600",
};

const GRADE_BG_DARK: Record<string, string> = {
  A: "bg-emerald-500/10",
  B: "bg-blue-500/10",
  C: "bg-amber-500/10",
  D: "bg-red-500/10",
};

const GRADE_BG_LIGHT: Record<string, string> = {
  A: "bg-emerald-50",
  B: "bg-blue-50",
  C: "bg-amber-50",
  D: "bg-red-50",
};

const COMPONENT_LABELS: Record<string, string> = {
  ic_mean: "IC 均值",
  ic_ir: "IC IR",
  stability: "稳定性",
  anti_overfit: "反过拟合",
  group_backtest: "分组回测",
};

function TestRow({ name, passed, details, isDark }: { name: string; passed: boolean; details: Record<string, unknown>; isDark: boolean }) {
  const [open, setOpen] = useState(false);

  const detailEntries = Object.entries(details).filter(([k]) => k !== "error");
  const error = details.error as string | undefined;

  return (
    <div className={`rounded-lg border ${isDark ? "border-gray-700" : "border-gray-100"}`}>
      <button
        onClick={() => setOpen(!open)}
        className={`w-full flex items-center gap-2 px-3 py-2 text-xs ${isDark ? "hover:bg-gray-800" : "hover:bg-gray-50"} rounded-lg transition-colors`}
      >
        <span className={`flex-shrink-0 w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold ${
          passed
            ? isDark ? "bg-emerald-500/20 text-emerald-400" : "bg-emerald-100 text-emerald-600"
            : isDark ? "bg-red-500/20 text-red-400" : "bg-red-100 text-red-600"
        }`}>
          {passed ? "✓" : "✗"}
        </span>
        <span className={`flex-1 text-left font-medium ${isDark ? "text-gray-300" : "text-gray-700"}`}>{name}</span>
        {error && <span className={`text-[10px] ${isDark ? "text-red-400" : "text-red-500"}`}>{error}</span>}
        {!error && detailEntries.length > 0 && (
          open ? <ChevronUp className="h-3 w-3 text-gray-400" /> : <ChevronDown className="h-3 w-3 text-gray-400" />
        )}
      </button>
      {open && detailEntries.length > 0 && (
        <div className={`px-3 pb-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] ${isDark ? "text-gray-400" : "text-gray-500"}`}>
          {detailEntries.map(([k, v]) => (
            <div key={k} className="flex justify-between">
              <span>{k}</span>
              <span className={`font-mono ${isDark ? "text-gray-300" : "text-gray-700"}`}>
                {typeof v === "number" ? v.toFixed(4) : typeof v === "object" ? JSON.stringify(v) : String(v)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RobustnessSection({ title, result, isDark }: { title: string; result: RobustnessResult; isDark: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const scoreColor = result.score >= 75 ? (isDark ? "text-emerald-400" : "text-emerald-600")
    : result.score >= 50 ? (isDark ? "text-amber-400" : "text-amber-600")
    : (isDark ? "text-red-400" : "text-red-600");

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        className={`w-full flex items-center justify-between py-1.5 text-xs ${isDark ? "hover:text-gray-200" : "hover:text-gray-900"} transition-colors`}
      >
        <span className={`font-medium ${isDark ? "text-gray-300" : "text-gray-600"}`}>{title}</span>
        <span className="flex items-center gap-2">
          <span className={scoreColor}>{result.passed_count}/{result.total_count} 通过 · {result.recommendation}</span>
          {expanded ? <ChevronUp className="h-3 w-3 text-gray-400" /> : <ChevronDown className="h-3 w-3 text-gray-400" />}
        </span>
      </button>
      {expanded && (
        <div className="mt-1 space-y-1">
          {result.tests.map((t, i) => (
            <TestRow key={i} name={t.name} passed={t.passed} details={t.details as Record<string, unknown>} isDark={isDark} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function RobustnessCard({ antiOverfit, adversarial, scoring }: Props) {
  const { isDark } = useColorMode();

  if (!scoring && !antiOverfit && !adversarial) return null;

  const grade = scoring?.grade ?? "D";
  const score = scoring?.score ?? 0;
  const gradeColor = isDark ? GRADE_COLORS_DARK[grade] : GRADE_COLORS_LIGHT[grade];
  const gradeBg = isDark ? GRADE_BG_DARK[grade] : GRADE_BG_LIGHT[grade];

  const totalTests = (antiOverfit?.total_count ?? 0) + (adversarial?.total_count ?? 0);
  const passedTests = (antiOverfit?.passed_count ?? 0) + (adversarial?.passed_count ?? 0);

  const ShieldIcon = passedTests >= totalTests * 0.75 ? ShieldCheck
    : passedTests >= totalTests * 0.5 ? Shield
    : ShieldAlert;

  const shieldColor = passedTests >= totalTests * 0.75 ? (isDark ? "text-emerald-400" : "text-emerald-600")
    : passedTests >= totalTests * 0.5 ? (isDark ? "text-amber-400" : "text-amber-600")
    : (isDark ? "text-red-400" : "text-red-600");

  return (
    <div className={`rounded-xl border ${isDark ? "border-gray-700 bg-gray-900" : "border-gray-200 bg-white"} px-4 py-3 space-y-3`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldIcon className={`h-4 w-4 ${shieldColor}`} />
          <span className={`text-xs font-medium ${isDark ? "text-gray-300" : "text-gray-700"}`}>因子质量评估</span>
        </div>
        {scoring && (
          <div className="flex items-center gap-2">
            <span className={`text-lg font-bold ${gradeColor}`}>{grade}</span>
            <span className={`text-xs px-2 py-0.5 rounded-full ${gradeBg} ${gradeColor} font-medium`}>
              {score.toFixed(1)} 分
            </span>
            {scoring.capped && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${isDark ? "bg-red-500/10 text-red-400" : "bg-red-50 text-red-600"}`}>
                {scoring.cap_reason === "negative_cagr" ? "负收益降级" : "负 Sharpe 降级"}
              </span>
            )}
          </div>
        )}
      </div>

      {scoring?.component_scores && (
        <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
          {Object.entries(scoring.component_scores).map(([k, v]) => (
            <div key={k} className={`text-center px-2 py-1.5 rounded-lg ${isDark ? "bg-gray-800" : "bg-gray-50"}`}>
              <div className={`text-[10px] ${isDark ? "text-gray-500" : "text-gray-400"}`}>{COMPONENT_LABELS[k] ?? k}</div>
              <div className={`text-sm font-medium ${
                v >= 80 ? (isDark ? "text-emerald-400" : "text-emerald-600")
                : v >= 50 ? (isDark ? "text-gray-200" : "text-gray-700")
                : (isDark ? "text-red-400" : "text-red-600")
              }`}>{v.toFixed(0)}</div>
            </div>
          ))}
        </div>
      )}

      {totalTests > 0 && (
        <div className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"} flex items-center gap-1`}>
          <span>稳健性检验：{passedTests}/{totalTests} 通过</span>
        </div>
      )}

      {antiOverfit && <RobustnessSection title="反过拟合检验 (4项)" result={antiOverfit} isDark={isDark} />}
      {adversarial && <RobustnessSection title="对抗性检验 (4项)" result={adversarial} isDark={isDark} />}
    </div>
  );
}
