"""Tests for trajectory_analyzer.py."""


from quantgpt.trajectory_analyzer import analyze_trajectory


class TestAnalyzeTrajectory:
    def test_empty_input(self):
        m = analyze_trajectory([])
        assert m.num_iterations == 0
        assert m.best_score == 0

    def test_single_iteration(self):
        m = analyze_trajectory([{"expression": "close", "score": 50}])
        assert m.num_iterations == 1
        assert m.best_score == 50
        assert m.best_expression == "close"

    def test_improving_trajectory(self):
        iters = [
            {"expression": f"expr_{i}", "score": 10 + i * 5}
            for i in range(10)
        ]
        m = analyze_trajectory(iters)
        assert m.convergence_rate > 0
        assert m.best_score == 55
        assert m.consecutive_declines == 0

    def test_declining_trajectory(self):
        iters = [
            {"expression": f"expr_{i}", "score": 80 - i * 5}
            for i in range(5)
        ]
        m = analyze_trajectory(iters)
        assert m.consecutive_declines == 4

    def test_stable_trajectory(self):
        iters = [{"expression": "x", "score": 50} for _ in range(10)]
        m = analyze_trajectory(iters)
        assert m.stability_score == 1.0
        assert m.exploration_diversity == 0.0

    def test_diverse_trajectory(self):
        iters = [
            {"expression": "a", "score": 10},
            {"expression": "b", "score": 90},
            {"expression": "c", "score": 20},
            {"expression": "d", "score": 80},
        ]
        m = analyze_trajectory(iters)
        assert m.exploration_diversity > 0.3

    def test_best_expression_found(self):
        iters = [
            {"expression": "low", "score": 30},
            {"expression": "best", "score": 90},
            {"expression": "mid", "score": 50},
        ]
        m = analyze_trajectory(iters)
        assert m.best_expression == "best"
        assert m.best_score == 90

    def test_none_scores_treated_as_zero(self):
        iters = [
            {"expression": "a", "score": None},
            {"expression": "b", "score": 50},
        ]
        m = analyze_trajectory(iters)
        assert m.best_score == 50

    def test_metrics_in_range(self):
        iters = [{"expression": f"e{i}", "score": i * 10} for i in range(1, 8)]
        m = analyze_trajectory(iters)
        assert 0 <= m.exploration_diversity <= 1
        assert 0 <= m.convergence_rate <= 1
        assert 0 <= m.stability_score <= 1
