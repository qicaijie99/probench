from datetime import UTC, datetime

from provider_bench.models import HardGate, PluginResult, RunStatus, ScoringConfig
from provider_bench.scoring import build_scorecard


def test_hard_gate_overrides_weighted_score() -> None:
    now = datetime.now(UTC)
    plugins = {
        "quality": PluginResult(
            name="quality",
            status=RunStatus.COMPLETED,
            started_at=now,
            ended_at=now,
            metrics={"score": 1.0, "success_rate": 1.0},
        )
    }
    config = ScoringConfig(
        gates=[HardGate(metric="quality.score", operator=">=", value=1.01, severity="fail")]
    )
    scorecard = build_scorecard(plugins, config)
    assert scorecard["score"] == 100
    assert scorecard["verdict"] == "FAIL"
    assert scorecard["gates"][0]["passed"] is False


def test_features_param_score_excludes_lenient_warnings() -> None:
    now = datetime.now(UTC)
    plugins = {
        "features": PluginResult(
            name="features",
            status=RunStatus.COMPLETED,
            started_at=now,
            ended_at=now,
            metrics={
                "param_constraints": {
                    "total": 13,
                    "passed": 6,
                    "failed": 0,
                    "warned": 7,
                    "success_rate": 1.0,
                },
                "thinking": {"total": 0, "passed": 0, "success_rate": 0.0},
                "reasoning_effort": {"total": 0, "passed": 0, "success_rate": 0.0},
            },
        )
    }
    scorecard = build_scorecard(plugins, ScoringConfig())
    assert scorecard["components"]["features_param"] == 100.0

