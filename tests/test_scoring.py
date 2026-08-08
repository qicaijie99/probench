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

