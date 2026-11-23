"""Tests for iteration module — compute_factor_score."""


from quantgpt.iteration import compute_factor_score, is_duplicate_expression


class TestComputeFactorScore:
    def _base_summary(self, **overrides):
        d = {
            "ic_mean": 0.03,
            "rank_ic_mean": 0.03,
            "ic_ir": 0.5,
            "ic_win_rate": 0.6,
            "long_short_sharpe": 1.0,
            "monotonicity_score": 0.7,
            "spread": 0.05,
            "turnover": 0.15,
        }
        d.update(overrides)
        return d

    def _base_report(self, **overrides):
        d = {"cagr": 0.10, "sharpe": 1.0}
        d.update(overrides)
        return d

    def test_returns_required_keys(self):
        result = compute_factor_score(self._base_summary(), self._base_report())
        assert "score" in result
        assert "grade" in result
        assert "component_scores" in result
        assert set(result["component_scores"].keys()) == {
            "ic_mean", "ic_ir", "stability", "anti_overfit", "group_backtest",
        }

    def test_score_in_range(self):
        result = compute_factor_score(self._base_summary(), self._base_report())
        assert 0 <= result["score"] <= 100

    def test_perfect_factor_gets_high_score(self):
        summary = self._base_summary(
            ic_mean=0.08, ic_ir=1.5, ic_win_rate=0.75,
            long_short_sharpe=2.0, monotonicity_score=1.0, spread=0.10,
            turnover=0.05,
        )
        result = compute_factor_score(summary, self._base_report(), anti_overfit_score=90)
        assert result["score"] >= 80
        assert result["grade"] == "A"

    def test_weak_factor_gets_low_score(self):
        summary = self._base_summary(
            ic_mean=0.005, ic_ir=0.1, ic_win_rate=0.50,
            long_short_sharpe=0.1, monotonicity_score=0.2, spread=-0.02,
        )
        result = compute_factor_score(summary, self._base_report(cagr=-0.05, sharpe=-0.3))
        assert result["score"] < 40
        assert result["grade"] in ("C", "D")

    def test_negative_cagr_caps_grade(self):
        summary = self._base_summary(
            ic_mean=0.08, ic_ir=1.5, ic_win_rate=0.75,
            long_short_sharpe=2.0, monotonicity_score=1.0, spread=0.10,
        )
        result = compute_factor_score(summary, self._base_report(cagr=-0.01))
        assert result["grade"] == "C"
        assert result["capped"] is True
        assert result["score"] <= 59.9

    def test_negative_sharpe_caps_grade(self):
        summary = self._base_summary(
            ic_mean=0.08, ic_ir=1.5, ic_win_rate=0.75,
            long_short_sharpe=2.0, monotonicity_score=1.0, spread=0.10,
        )
        result = compute_factor_score(summary, self._base_report(sharpe=-0.5))
        assert result["capped"] is True
        assert result["cap_reason"] == "negative_sharpe"

    def test_anti_overfit_none_defaults_to_50(self):
        result = compute_factor_score(self._base_summary(), self._base_report(), anti_overfit_score=None)
        assert result["component_scores"]["anti_overfit"] == 50.0

    def test_anti_overfit_clamped(self):
        result = compute_factor_score(self._base_summary(), self._base_report(), anti_overfit_score=150)
        assert result["component_scores"]["anti_overfit"] == 100.0
        result2 = compute_factor_score(self._base_summary(), self._base_report(), anti_overfit_score=-20)
        assert result2["component_scores"]["anti_overfit"] == 0.0

    def test_zero_inputs(self):
        summary = self._base_summary(
            ic_mean=0, ic_ir=0, ic_win_rate=0.5,
            long_short_sharpe=0, monotonicity_score=0, spread=0,
        )
        result = compute_factor_score(summary, self._base_report(cagr=0, sharpe=0))
        assert result["score"] >= 0


class TestIsDuplicateExpression:
    def test_exact_duplicate(self):
        assert is_duplicate_expression("rank(close)", ["rank(close)", "rank(volume)"])

    def test_whitespace_difference_is_duplicate(self):
        assert is_duplicate_expression("rank( close )", ["rank(close)"])

    def test_not_duplicate(self):
        assert not is_duplicate_expression("rank(volume)", ["rank(close)"])

    def test_empty_existing(self):
        assert not is_duplicate_expression("rank(close)", [])
