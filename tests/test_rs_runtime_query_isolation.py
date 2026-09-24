"""Runtime recommendations must ignore benchmark answer fields."""

from dataclasses import replace

from recommender.dataset import IncidentInteraction
from recommender.hybrid import HybridRecommender


def test_benchmark_root_cause_cannot_change_runtime_ranking() -> None:
    rows = [
        IncidentInteraction(
            interaction_id=f"row-{index}",
            incident_id=f"inc-{index}",
            scenario_id=f"scenario-{index}",
            split="train",
            tier=tier,
            alertname="HighCPUUsage",
            affected_services=["paymentservice"],
            symptoms_text="Observed CPU utilization is elevated",
            relevant_runbook_id=runbook,
            rating=1.0,
        )
        for index, (tier, runbook) in enumerate(
            [("single_fault", "RB-CPU-THROTTLE"), ("cascade", "RB-CASCADE-HEAL")]
        )
    ]
    recommender = HybridRecommender().fit(rows)
    observed_query = {
        "alertname": "HighCPUUsage",
        "affected_services": ["paymentservice"],
        "symptoms_text": "Observed CPU utilization is elevated",
        "tier": "single_fault",
    }
    baseline = recommender.recommend_runbooks(observed_query, k=3)

    for hidden_answer in ("CPU stress", "pod memory failure"):
        contaminated_query = {**observed_query, "expected_root_cause": hidden_answer}
        assert recommender.recommend_runbooks(contaminated_query, k=3) == baseline

    for benchmark_tier in ("single_fault", "cascade", "multi_fault"):
        contaminated_query = {**observed_query, "tier": benchmark_tier}
        assert recommender.recommend_runbooks(contaminated_query, k=3) == baseline

    interaction_query = rows[0]
    assert recommender.recommend_runbooks(
        replace(interaction_query, tier="cascade"), k=3
    ) == recommender.recommend_runbooks(interaction_query, k=3)

    assert all("historical recovery success" not in item.explanation for item in baseline)
