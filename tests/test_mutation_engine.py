"""Tests for mutation_engine.py — diagnosis and mutation strategy selection."""


from quantgpt.mutation_engine import MutationEngine, MutationStrategy


def _make_engine(expression="rank(ts_mean(close, 20))", score=50, ic_mean=0.03,
                 ic_ir=0.8, mono=0.7):
    return MutationEngine(
        expression=expression,
        metrics={
            "backtest_summary": {
                "ic_mean": ic_mean,
                "ic_ir": ic_ir,
                "monotonicity_score": mono,
            },
            "report_metrics": {},
        },
        score=score,
    )


class TestDiagnoseFailure:
    def test_very_low_score_regenerates(self):
        d = _make_engine(score=10).diagnose_failure()
        assert d.strategy == MutationStrategy.REGENERATE_FULL

    def test_zero_ic_mutates_operator(self):
        d = _make_engine(ic_mean=0.001).diagnose_failure()
        assert d.strategy == MutationStrategy.MUTATE_OPERATOR

    def test_negative_ic_mutates_signal(self):
        d = _make_engine(ic_mean=-0.05).diagnose_failure()
        assert d.strategy == MutationStrategy.MUTATE_SIGNAL_TYPE

    def test_deep_nesting_simplifies(self):
        deep = "rank(" * 10 + "close" + ")" * 10
        d = _make_engine(expression=deep, score=40).diagnose_failure()
        assert d.strategy == MutationStrategy.SIMPLIFY

    def test_medium_score_no_nonlinear_adds_nonlinear(self):
        d = _make_engine(expression="ts_mean(close, 20)", score=35).diagnose_failure()
        assert d.strategy == MutationStrategy.MUTATE_NONLINEAR

    def test_low_ir_no_norm_adds_normalization(self):
        d = _make_engine(expression="ts_mean(close, 20)", score=55, ic_ir=0.3).diagnose_failure()
        assert d.strategy == MutationStrategy.MUTATE_NORMALIZATION

    def test_single_signal_adds_interaction(self):
        d = _make_engine(expression="rank(tanh(ts_mean(close, 20)))", score=55, ic_ir=0.6).diagnose_failure()
        assert d.strategy == MutationStrategy.MUTATE_INTERACTION

    def test_default_mutates_window(self):
        d = _make_engine(expression="rank(ts_mean(close, 20)) * rank(ts_std(volume, 10))",
                         score=55, ic_ir=0.6).diagnose_failure()
        assert d.strategy == MutationStrategy.MUTATE_WINDOW


class TestBuildMutationPrompt:
    def test_returns_two_strings(self):
        sys_p, user_p = _make_engine().build_mutation_prompt()
        assert isinstance(sys_p, str)
        assert isinstance(user_p, str)

    def test_system_prompt_has_format_rules(self):
        sys_p, _ = _make_engine().build_mutation_prompt()
        assert "输出格式" in sys_p

    def test_user_prompt_has_expression_and_score(self):
        _, user_p = _make_engine(expression="rank(close)", score=42).build_mutation_prompt()
        assert "rank(close)" in user_p
        assert "42" in user_p

    def test_operators_doc_included(self):
        sys_p, _ = _make_engine().build_mutation_prompt(operators_doc="## My Operators")
        assert "My Operators" in sys_p

    def test_each_strategy_produces_prompt(self):
        configs = [
            {"score": 10},
            {"ic_mean": 0.001},
            {"ic_mean": -0.05},
            {"expression": "rank(" * 10 + "close" + ")" * 10, "score": 40},
            {"expression": "ts_mean(close, 20)", "score": 35},
            {"expression": "ts_mean(close, 20)", "score": 55, "ic_ir": 0.3},
            {"expression": "rank(tanh(ts_mean(close, 20)))", "score": 55, "ic_ir": 0.6},
        ]
        for cfg in configs:
            sys_p, user_p = _make_engine(**cfg).build_mutation_prompt()
            assert len(sys_p) > 100
            assert len(user_p) > 100


class TestHelpers:
    def test_count_nesting(self):
        e = _make_engine(expression="rank(ts_mean(close, 20))")
        assert e._count_nesting("rank(ts_mean(close, 20))") == 2
        assert e._count_nesting("close") == 0

    def test_has_normalization(self):
        e = _make_engine()
        assert e._has_normalization("rank(close)")
        assert e._has_normalization("zscore(volume)")
        assert not e._has_normalization("ts_mean(close, 20)")

    def test_has_nonlinear(self):
        e = _make_engine()
        assert e._has_nonlinear("tanh(close)")
        assert e._has_nonlinear("power(close, 2)")
        assert not e._has_nonlinear("rank(close)")

    def test_is_single_signal(self):
        assert _make_engine(expression="rank(ts_mean(close, 20))")._is_single_signal()
        assert not _make_engine(expression="rank(close) * rank(volume)")._is_single_signal()

    def test_extract_windows(self):
        e = _make_engine(expression="ts_mean(close, 20) / ts_std(close, 10)")
        assert e._extract_windows() == [10, 20]

    def test_suggest_replacements(self):
        e = _make_engine(expression="ts_mean(close, 20)")
        suggestions = e._suggest_replacements()
        assert "ts_mean" in suggestions
        assert "decay_linear" in suggestions["ts_mean"]
