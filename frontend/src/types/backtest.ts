export type TaskStatus =
  | "pending"
  | "generating_expression"
  | "validating"
  | "fetching_data"
  | "fetching_fundamentals"
  | "backtesting"
  | "analyzing"
  | "generating_report"
  | "completed"
  | "failed"
  | "cancelled"
  | "iterating"
  | "iteration_completed";

export interface BacktestRequest {
  prompt: string;
  universe?: string;
  start_date?: string;
  end_date?: string;
  n_groups?: number;
  holding_period?: number;
  benchmark?: string;
  neutralize_industry?: boolean;
  neutralize_cap?: boolean;
}

export interface BacktestMetrics {
  total_return: number;
  cagr: number;
  sharpe: number;
  sortino: number;
  max_drawdown: number;
  volatility: number;
  win_rate: number;
  profit_factor: number;
  benchmark_total_return?: number | null;
  benchmark_cagr?: number | null;
}

export interface GroupReturn {
  group: string;
  annual_return: number;
  sharpe: number;
  max_drawdown: number;
}

export interface IterationCandidate {
  expression: string;
  score: number;
  grade: "A" | "B" | "C" | "D";
  component_scores: Record<string, number>;
  backtest_summary: {
    long_short_sharpe: number;
    long_short_annual?: number;
    top_group_sharpe?: number;
    monotonicity_score: number;
    spread: number;
    group_returns: Record<string, GroupReturn>;
    ic_mean?: number;
    rank_ic_mean?: number;
    ic_ir?: number;
    ic_win_rate?: number;
    turnover?: number;
    wq_fitness?: number;
  };
  report_metrics: BacktestMetrics;
  report_url: string;
  status: "success" | "failed";
  error?: string;
}

export interface StockFactorInfo {
  stock_code: string;
  factor_value: number;
  factor_rank: number;
  group: number;
  group_label: string;
  period_return: number;
}

export interface StockFactorData {
  rebalance_date: string;
  flipped: boolean;
  total_stock_count: number;
  stocks: StockFactorInfo[];
}

export interface FactorInterpretation {
  logic: string;
  source: string;
  guidance: string;
  risk: string;
  rating?: string;
  rating_reason?: string;
  conclusion?: string;
  suggestions?: string[];
}

export interface WQISTest {
  value: number;
  threshold?: number;
  threshold_min?: number;
  threshold_max?: number;
  label: string;
  pass: boolean;
}

export interface WQSubUniverse {
  sub_sharpe_a: number;
  sub_sharpe_b: number;
  sub_sharpe_min: number;
  threshold: number;
  pass: boolean;
}

export interface WQBrain {
  wq_sharpe: number;
  wq_turnover: number;
  wq_returns: number;
  wq_fitness: number;
  wq_max_weight: number;
  wq_rating: string;
  margin_bps: number;
  submittable: boolean;
  sub_universe: WQSubUniverse;
  wq_is_tests: Record<string, WQISTest>;
}

export interface RobustnessTest {
  name: string;
  passed: boolean;
  details: Record<string, unknown>;
}

export interface RobustnessResult {
  score: number;
  recommendation: string;
  passed_count: number;
  total_count: number;
  tests: RobustnessTest[];
}

export interface ScoringResult {
  score: number;
  grade: "A" | "B" | "C" | "D";
  component_scores: Record<string, number>;
  capped?: boolean;
  cap_reason?: string;
}

export interface BacktestResult {
  report_url: string;
  metrics: BacktestMetrics;
  wq_brain?: WQBrain;
  anti_overfit?: RobustnessResult | null;
  adversarial?: RobustnessResult | null;
  scoring?: ScoringResult | null;
  backtest_summary: {
    long_short_sharpe: number;
    long_short_annual?: number;
    top_group_sharpe?: number;
    monotonicity_score: number;
    spread: number;
    group_returns: Record<string, GroupReturn>;
    ic_mean?: number;
    rank_ic_mean?: number;
    ic_ir?: number;
    ic_win_rate?: number;
    turnover?: number;
    wq_fitness?: number;
  };
  params: {
    expression: string;
    universe: string;
    start_date: string;
    end_date: string;
    n_groups: number;
    holding_period: number;
    benchmark: string;
    stock_count: number;
  };
  llm: {
    prompt: string;
    generated_expression: string;
  };
  interpretation?: FactorInterpretation;
  stock_factor_data?: StockFactorData | null;
  nav_series?: { date: string; value: number }[];
}

export interface Session {
  id: string;
  name: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface Task {
  task_id: string;
  status: TaskStatus;
  session_id?: string;
  params?: BacktestRequest;
  expression?: string;
  error?: string;
  result?: BacktestResult;
  task_type?: "backtest" | "iteration" | "composite";
  parent_task_id?: string;
  candidates?: IterationCandidate[];
  candidates_done?: number;
  candidates_total?: number;
  selected_candidate_index?: number;
}
