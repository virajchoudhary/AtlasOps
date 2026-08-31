"""AtlasOps Frozen Scenario Catalog & Benchmark Split Registry (Gate G5).

This module formalizes and freezes the complete 28-scenario catalog, ground-truth
root-cause definitions, objective verification predicates, cryptographic manifest
hashes, and disjoint Train/Validation/Test benchmark splits.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── Split Random Seeds ────────────────────────────────────────────────────────
TRAIN_SEED: int = 2026
VAL_SEED: int = 1337
TEST_SEED: int = 42
LEADERBOARD_SEED: int = 777


@dataclass(frozen=True)
class ScenarioMetadata:
    """Immutable ground-truth metadata for an AtlasOps incident scenario."""
    scenario_id: str
    tier: str
    manifest_relpath: str
    manifest_sha256: str
    doc_count: int
    chaos_kinds: tuple[str, ...]
    target_services: tuple[str, ...]
    expected_alert: str
    expected_root_cause: str
    verification_workloads: tuple[str, ...]
    require_chaos_cleared: bool = True
    require_no_legacy_deployments: tuple[str, ...] = ()
    description: str = ""


# ── Complete Frozen 28-Scenario Catalog ───────────────────────────────────────

SCENARIO_CATALOG: dict[str, ScenarioMetadata] = {
    # ── Single-Fault (8 scenarios) ────────────────────────────────────────────
    "single_fault/sf-001": ScenarioMetadata(
        scenario_id="single_fault/sf-001",
        tier="single_fault",
        manifest_relpath="bench/chaos_manifests/single_fault/sf-001.yaml",
        manifest_sha256="90c402c4f88465c13954531fdc5019788a98de8137ea3c157d231d448519800b",
        doc_count=1,
        chaos_kinds=("PodChaos",),
        target_services=("cartservice",),
        expected_alert="OnlineBoutiqueCartServiceDown",
        expected_root_cause="Pod kill on cartservice pod causing container restarts and service outage",
        verification_workloads=("cartservice",),
        description="PodChaos pod-kill targeting cartservice deployment in default namespace",
    ),
    "single_fault/sf-002": ScenarioMetadata(
        scenario_id="single_fault/sf-002",
        tier="single_fault",
        manifest_relpath="bench/chaos_manifests/single_fault/sf-002.yaml",
        manifest_sha256="06686ac0645bd039af56ae1fb0dfd787f8010c8086d524e3b92406180f9575e7",
        doc_count=1,
        chaos_kinds=("StressChaos",),
        target_services=("paymentservice",),
        expected_alert="HighCpuUsage",
        expected_root_cause="CPU stress saturation (4 workers @ 90% load) on paymentservice pod",
        verification_workloads=("paymentservice",),
        description="StressChaos CPU stress targeting paymentservice container",
    ),
    "single_fault/sf-003": ScenarioMetadata(
        scenario_id="single_fault/sf-003",
        tier="single_fault",
        manifest_relpath="bench/chaos_manifests/single_fault/sf-003.yaml",
        manifest_sha256="4dfb91c8f36f19cc219df35b1d62a70c0984c8c9a809d326a6c78ef424968dde",
        doc_count=1,
        chaos_kinds=("StressChaos",),
        target_services=("checkoutservice",),
        expected_alert="HighMemoryUsage",
        expected_root_cause="Memory stress allocation on checkoutservice pod causing high memory pressure",
        verification_workloads=("checkoutservice",),
        description="StressChaos memory stress targeting checkoutservice container",
    ),
    "single_fault/sf-004": ScenarioMetadata(
        scenario_id="single_fault/sf-004",
        tier="single_fault",
        manifest_relpath="bench/chaos_manifests/single_fault/sf-004.yaml",
        manifest_sha256="b78f35d8562ef6414d5243378079110fe00b2bc5e5fff7f0e67a1b342bafa918",
        doc_count=1,
        chaos_kinds=("NetworkChaos",),
        target_services=("frontend",),
        expected_alert="High5xxErrorRate",
        expected_root_cause="Network packet loss (50%) on frontend ingress traffic",
        verification_workloads=("frontend",),
        description="NetworkChaos loss targeting frontend pod network interfaces",
    ),
    "single_fault/sf-005": ScenarioMetadata(
        scenario_id="single_fault/sf-005",
        tier="single_fault",
        manifest_relpath="bench/chaos_manifests/single_fault/sf-005.yaml",
        manifest_sha256="0b896f22557f1319e2bab7f7f2db5ff02c89754fe545b81de2088444ad270046",
        doc_count=1,
        chaos_kinds=("NetworkChaos",),
        target_services=("redis-cart", "cartservice"),
        expected_alert="CartServiceRedisConnectionFailure",
        expected_root_cause="Network partition isolating redis-cart from cartservice",
        verification_workloads=("redis-cart", "cartservice"),
        description="NetworkChaos partition between redis-cart state store and cartservice",
    ),
    "single_fault/sf-006": ScenarioMetadata(
        scenario_id="single_fault/sf-006",
        tier="single_fault",
        manifest_relpath="bench/chaos_manifests/single_fault/sf-006.yaml",
        manifest_sha256="886e4873b3f71761327751c41f7250202b0dda6c8a78e02536c6df0f526e8b28",
        doc_count=1,
        chaos_kinds=("DNSChaos",),
        target_services=("checkoutservice",),
        expected_alert="CheckoutServiceDnsResolutionFailure",
        expected_root_cause="DNSChaos random DNS query failure targeting checkoutservice upstream lookups",
        verification_workloads=("checkoutservice",),
        description="DNSChaos random DNS resolution failure targeting checkoutservice",
    ),
    "single_fault/sf-007": ScenarioMetadata(
        scenario_id="single_fault/sf-007",
        tier="single_fault",
        manifest_relpath="bench/chaos_manifests/single_fault/sf-007.yaml",
        manifest_sha256="3e15a122b34aed52a4cee16f315606b7cd4058acdd05019777c9ec65fa3e2c06",
        doc_count=1,
        chaos_kinds=("IOChaos",),
        target_services=("emailservice",),
        expected_alert="EmailServiceIoError",
        expected_root_cause="IOChaos filesystem error/full condition on emailservice data volume",
        verification_workloads=("emailservice",),
        description="IOChaos disk fault / IO delay targeting emailservice",
    ),
    "single_fault/sf-008": ScenarioMetadata(
        scenario_id="single_fault/sf-008",
        tier="single_fault",
        manifest_relpath="bench/chaos_manifests/single_fault/sf-008.yaml",
        manifest_sha256="41d3f6d2ed0278bbc101740f738575bcd5eccf97e8971b17308e39e3dab1ac3b",
        doc_count=1,
        chaos_kinds=("TimeChaos",),
        target_services=("paymentservice",),
        expected_alert="PaymentAuthClockSkew",
        expected_root_cause="TimeChaos clock skew (10m offset) invalidating token auth on paymentservice",
        verification_workloads=("paymentservice",),
        description="TimeChaos clock skew targeting paymentservice container",
    ),

    # ── Cascade (5 scenarios) ─────────────────────────────────────────────────
    "cascade/cs-001": ScenarioMetadata(
        scenario_id="cascade/cs-001",
        tier="cascade",
        manifest_relpath="bench/chaos_manifests/cascade/cs-001.yaml",
        manifest_sha256="6b777ce506cde061236cf340db78074d29487be9ae4ed32d8a06b169f1e153a7",
        doc_count=1,
        chaos_kinds=("NetworkChaos",),
        target_services=("currencyservice",),
        expected_alert="CurrencyConversionLatencyHigh",
        expected_root_cause="NetworkChaos latency on currencyservice propagating upstream timeouts to frontend",
        verification_workloads=("currencyservice",),
        description="Cascading latency injected into currencyservice impacting frontend conversion flows",
    ),
    "cascade/cs-002": ScenarioMetadata(
        scenario_id="cascade/cs-002",
        tier="cascade",
        manifest_relpath="bench/chaos_manifests/cascade/cs-002.yaml",
        manifest_sha256="05e36fdd8c48e0d81148ce233fb86755fc4b2e53a3964e6ae8e583af742183c7",
        doc_count=1,
        chaos_kinds=("NetworkChaos",),
        target_services=("redis-cart",),
        expected_alert="RedisCartPartitionCascade",
        expected_root_cause="NetworkChaos partition on redis-cart cascading into cartservice and checkout failures",
        verification_workloads=("redis-cart",),
        description="Redis-cart network partition cascading through shopping cart workflows",
    ),
    "cascade/cs-003": ScenarioMetadata(
        scenario_id="cascade/cs-003",
        tier="cascade",
        manifest_relpath="bench/chaos_manifests/cascade/cs-003.yaml",
        manifest_sha256="7301692096c221919e1a005341cb8348052186016f709441d35471321d7c1771",
        doc_count=1,
        chaos_kinds=("StressChaos",),
        target_services=("recommendationservice",),
        expected_alert="RecommendationServiceCpuStarvation",
        expected_root_cause="StressChaos CPU starvation on recommendationservice causing frontend render slowdown",
        verification_workloads=("recommendationservice",),
        description="CPU starvation on recommendationservice cascading into product page latency",
    ),
    "cascade/cs-004": ScenarioMetadata(
        scenario_id="cascade/cs-004",
        tier="cascade",
        manifest_relpath="bench/chaos_manifests/cascade/cs-004.yaml",
        manifest_sha256="7b5a11f3a4f679d2cde7decf6a92e32dbfea4f4fb13d60831ef091257b2fec79",
        doc_count=1,
        chaos_kinds=("IOChaos",),
        target_services=("emailservice",),
        expected_alert="EmailServiceIoFailureCascade",
        expected_root_cause="IOChaos disk full on emailservice blocking order confirmation completions",
        verification_workloads=("emailservice",),
        description="Disk write exhaustion on emailservice cascading into order finalization stalls",
    ),
    "cascade/cs-005": ScenarioMetadata(
        scenario_id="cascade/cs-005",
        tier="cascade",
        manifest_relpath="bench/chaos_manifests/cascade/cs-005.yaml",
        manifest_sha256="448df63030582a2390360b1064bbc81c529b7f6060d803a02606e72330e261b5",
        doc_count=2,
        chaos_kinds=("NetworkChaos", "StressChaos"),
        target_services=("cartservice",),
        expected_alert="CartServiceLatencyAndMemoryStress",
        expected_root_cause="Compound NetworkChaos latency and StressChaos memory pressure on cartservice",
        verification_workloads=("cartservice",),
        description="Dual-vector network latency and memory stress compound cascade on cartservice",
    ),

    # ── Multi-Fault (5 scenarios) ─────────────────────────────────────────────
    "multi_fault/mf-001": ScenarioMetadata(
        scenario_id="multi_fault/mf-001",
        tier="multi_fault",
        manifest_relpath="bench/chaos_manifests/multi_fault/mf-001.yaml",
        manifest_sha256="1e2b6a68107dfbec9c3ee4c524a82aef6deb5eceb42d122f4bba4b451c49a852",
        doc_count=2,
        chaos_kinds=("NetworkChaos", "StressChaos"),
        target_services=("frontend", "checkoutservice"),
        expected_alert="MultiFaultFrontendLossCheckoutCpu",
        expected_root_cause="Simultaneous NetworkChaos loss on frontend and StressChaos CPU on checkoutservice",
        verification_workloads=("frontend", "checkoutservice"),
        description="Dual fault injecting frontend packet loss alongside checkoutservice CPU starvation",
    ),
    "multi_fault/mf-002": ScenarioMetadata(
        scenario_id="multi_fault/mf-002",
        tier="multi_fault",
        manifest_relpath="bench/chaos_manifests/multi_fault/mf-002.yaml",
        manifest_sha256="1a656644a99d8da25f26bfa6030359b84658d2e921fecf9126226516b5741334",
        doc_count=2,
        chaos_kinds=("NetworkChaos", "StressChaos"),
        target_services=("redis-cart", "cartservice", "recommendationservice"),
        expected_alert="MultiFaultCartPartitionRecMemory",
        expected_root_cause="Simultaneous network partition on redis-cart/cartservice and memory exhaustion on recommendationservice",
        verification_workloads=("redis-cart", "cartservice", "recommendationservice"),
        description="Multi-target fault: Redis partition plus recommendation service memory pressure",
    ),
    "multi_fault/mf-003": ScenarioMetadata(
        scenario_id="multi_fault/mf-003",
        tier="multi_fault",
        manifest_relpath="bench/chaos_manifests/multi_fault/mf-003.yaml",
        manifest_sha256="447ac4edf080f2ad141f3ff7bcf8b60420f31d6117eae767e5d0cd776103221e",
        doc_count=2,
        chaos_kinds=("DNSChaos", "NetworkChaos"),
        target_services=("currencyservice",),
        expected_alert="MultiFaultDnsAndLatency",
        expected_root_cause="Concurrent DNS query random drop across cluster with network delay on currencyservice",
        verification_workloads=("currencyservice",),
        description="DNSChaos random drop coupled with NetworkChaos delay on currency service",
    ),
    "multi_fault/mf-004": ScenarioMetadata(
        scenario_id="multi_fault/mf-004",
        tier="multi_fault",
        manifest_relpath="bench/chaos_manifests/multi_fault/mf-004.yaml",
        manifest_sha256="0eff6068d2c5813d2d35770c288d1b65bd0f728d4a561769c7a387a3b73ad7c4",
        doc_count=2,
        chaos_kinds=("TimeChaos", "NetworkChaos"),
        target_services=("paymentservice", "cartservice"),
        expected_alert="MultiFaultClockSkewCartCorrupt",
        expected_root_cause="Concurrent clock skew on paymentservice and packet corruption on cartservice",
        verification_workloads=("paymentservice", "cartservice"),
        description="TimeChaos clock drift on payment combined with network packet corruption on cart",
    ),
    "multi_fault/mf-005": ScenarioMetadata(
        scenario_id="multi_fault/mf-005",
        tier="multi_fault",
        manifest_relpath="bench/chaos_manifests/multi_fault/mf-005.yaml",
        manifest_sha256="959048e58473ffe29d84a32fee9902d98e0a36f7c0db867932210e8dfbbbc968",
        doc_count=2,
        chaos_kinds=("IOChaos", "NetworkChaos"),
        target_services=("emailservice", "checkoutservice"),
        expected_alert="MultiFaultIoAndNetworkDelay",
        expected_root_cause="Concurrent disk IO failure on emailservice and network delay on checkoutservice",
        verification_workloads=("emailservice", "checkoutservice"),
        description="IOChaos disk fault on email coupled with NetworkChaos delay on checkout",
    ),

    # ── Named Historical Replays (10 scenarios) ──────────────────────────────
    "named_replays/hist-aws-s3-2017": ScenarioMetadata(
        scenario_id="named_replays/hist-aws-s3-2017",
        tier="named_replays",
        manifest_relpath="bench/chaos_manifests/named_replays/hist-aws-s3-2017.yaml",
        manifest_sha256="55e3babadc529686e872f9d3800304edda3f4ad9ec55e7e25be7ad2c8ca69c78",
        doc_count=1,
        chaos_kinds=("Application",),
        target_services=("productcatalogservice",),
        expected_alert="ProductCatalogServiceDown",
        expected_root_cause="Command line mistake scaling productcatalogservice replicas to 0 via Argo CD patch",
        verification_workloads=("productcatalogservice",),
        description="Replay of AWS S3 2017 outage: unintended capacity removal scaling deployment to 0",
    ),
    "named_replays/hist-azure-dns-2019": ScenarioMetadata(
        scenario_id="named_replays/hist-azure-dns-2019",
        tier="named_replays",
        manifest_relpath="bench/chaos_manifests/named_replays/hist-azure-dns-2019.yaml",
        manifest_sha256="fa78984d8e099db6e3c20cbbc685d0a26d1cf99be3512fd0b607e892059a5db4",
        doc_count=1,
        chaos_kinds=("DNSChaos",),
        target_services=("checkoutservice", "cartservice", "currencyservice"),
        expected_alert="AzureDnsGlobalOutage",
        expected_root_cause="DNSChaos random DNS lookup failure across inter-service RPC mesh",
        verification_workloads=("checkoutservice", "cartservice", "currencyservice"),
        description="Replay of Azure DNS 2019 outage: global DNS name resolution degradation",
    ),
    "named_replays/hist-cloudflare-2019": ScenarioMetadata(
        scenario_id="named_replays/hist-cloudflare-2019",
        tier="named_replays",
        manifest_relpath="bench/chaos_manifests/named_replays/hist-cloudflare-2019.yaml",
        manifest_sha256="f85f73d68869c2256fd2340017470380e62ff170d899bc2e62d13c1a22d70490",
        doc_count=1,
        chaos_kinds=("StressChaos",),
        target_services=("frontend",),
        expected_alert="CloudflareCpuSaturation",
        expected_root_cause="StressChaos 100% CPU saturation on frontend (WAF regex catastrophic backtracking)",
        verification_workloads=("frontend",),
        description="Replay of Cloudflare 2019 outage: regex-induced CPU spike on proxy/frontend",
    ),
    "named_replays/hist-datadog-2023": ScenarioMetadata(
        scenario_id="named_replays/hist-datadog-2023",
        tier="named_replays",
        manifest_relpath="bench/chaos_manifests/named_replays/hist-datadog-2023.yaml",
        manifest_sha256="cfa781546a1953f00e500ef79465870596542c6f7e584970471a070ea36ecda2",
        doc_count=1,
        chaos_kinds=("DNSChaos",),
        target_services=("frontend", "cartservice", "checkoutservice", "productcatalogservice"),
        expected_alert="DatadogSystemdDnsOutage",
        expected_root_cause="DNSChaos systemd-resolved failure blocking CoreDNS resolution",
        verification_workloads=("frontend", "cartservice", "checkoutservice", "productcatalogservice"),
        description="Replay of Datadog 2023 outage: systemd-resolved OS-level DNS failure across nodes",
    ),
    "named_replays/hist-discord-2022": ScenarioMetadata(
        scenario_id="named_replays/hist-discord-2022",
        tier="named_replays",
        manifest_relpath="bench/chaos_manifests/named_replays/hist-discord-2022.yaml",
        manifest_sha256="e74f931dc0c70872bfd5f96d35c27e9263fd19c9b4c7d6e477a4e5bc368d9741",
        doc_count=2,
        chaos_kinds=("PodChaos", "NetworkChaos"),
        target_services=("redis-cart", "cartservice"),
        expected_alert="DiscordTreeFailure",
        expected_root_cause="Simultaneous pod kill on redis-cart and network latency on cartservice",
        verification_workloads=("redis-cart", "cartservice"),
        description="Replay of Discord 2022 outage: database failover accompanied by network saturation",
    ),
    "named_replays/hist-facebook-bgp-2021": ScenarioMetadata(
        scenario_id="named_replays/hist-facebook-bgp-2021",
        tier="named_replays",
        manifest_relpath="bench/chaos_manifests/named_replays/hist-facebook-bgp-2021.yaml",
        manifest_sha256="4a99827df3a0402f2a3e8e5038caebd588d350dcb70fdd1b13de98f3ccf46254",
        doc_count=1,
        chaos_kinds=("NetworkChaos",),
        target_services=("frontend",),
        expected_alert="FacebookBgpWithdrawal",
        expected_root_cause="NetworkChaos partition severing default namespace from kube-system DNS/BGP routing",
        verification_workloads=("frontend",),
        description="Replay of Facebook 2021 outage: total network partition isolating DNS/routing infrastructure",
    ),
    "named_replays/hist-fastly-2021": ScenarioMetadata(
        scenario_id="named_replays/hist-fastly-2021",
        tier="named_replays",
        manifest_relpath="bench/chaos_manifests/named_replays/hist-fastly-2021.yaml",
        manifest_sha256="957335d19cbd4a955362fedb7a7ae8454d4a9618c9b175740a7c19594f309735",
        doc_count=1,
        chaos_kinds=("NetworkChaos",),
        target_services=("frontend",),
        expected_alert="FastlyEdgeCorruptOutage",
        expected_root_cause="NetworkChaos corrupt (60%) packet corruption on edge frontend service",
        verification_workloads=("frontend",),
        description="Replay of Fastly 2021 outage: configuration bug triggering corrupt edge HTTP responses",
    ),
    "named_replays/hist-github-2018": ScenarioMetadata(
        scenario_id="named_replays/hist-github-2018",
        tier="named_replays",
        manifest_relpath="bench/chaos_manifests/named_replays/hist-github-2018.yaml",
        manifest_sha256="72570683734d6165b3f1d755c928a810cd4109ac9946ace7900f2434954e6a62",
        doc_count=1,
        chaos_kinds=("PodChaos",),
        target_services=("redis-cart",),
        expected_alert="GithubPartitionLeaderPartition",
        expected_root_cause="PodChaos kill on primary database state pod (redis-cart)",
        verification_workloads=("redis-cart",),
        description="Replay of GitHub 2018 outage: partition and unexpected primary database leader kill",
    ),
    "named_replays/hist-knight-capital-2012": ScenarioMetadata(
        scenario_id="named_replays/hist-knight-capital-2012",
        tier="named_replays",
        manifest_relpath="bench/chaos_manifests/named_replays/hist-knight-capital-2012.yaml",
        manifest_sha256="45fd37cd50720c407590679005c7131965398e5c89b1f01ce031e5d51444e069",
        doc_count=1,
        chaos_kinds=("Deployment",),
        target_services=("checkoutservice",),
        expected_alert="KnightCapitalDeadCodeActivation",
        expected_root_cause="Deployment of checkoutservice-legacy activating deprecated buggy code path",
        verification_workloads=("checkoutservice",),
        require_no_legacy_deployments=("checkoutservice-legacy",),
        description="Replay of Knight Capital 2012: accidental deployment of dormant legacy code",
    ),
    "named_replays/hist-slack-2022": ScenarioMetadata(
        scenario_id="named_replays/hist-slack-2022",
        tier="named_replays",
        manifest_relpath="bench/chaos_manifests/named_replays/hist-slack-2022.yaml",
        manifest_sha256="ab6f396bff921f960ae7ab12350c3241847f05e54a494378e123bfab08e6e5d4",
        doc_count=2,
        chaos_kinds=("NetworkChaos",),
        target_services=("frontend", "checkoutservice"),
        expected_alert="SlackDatabaseFailoverStorm",
        expected_root_cause="NetworkChaos packet duplication on frontend and packet delay on checkoutservice",
        verification_workloads=("frontend", "checkoutservice"),
        description="Replay of Slack 2022 outage: database failover triggering packet duplication and query storms",
    ),
}

FROZEN_STATIC_SCENARIO_COUNT: int = len(SCENARIO_CATALOG)


# ── Formal Benchmark Splits ───────────────────────────────────────────────────

TRAIN_SPLIT: tuple[str, ...] = (
    # Single Fault (5)
    "single_fault/sf-001",
    "single_fault/sf-002",
    "single_fault/sf-003",
    "single_fault/sf-004",
    "single_fault/sf-005",
    # Cascade (3)
    "cascade/cs-001",
    "cascade/cs-002",
    "cascade/cs-003",
    # Multi Fault (3)
    "multi_fault/mf-001",
    "multi_fault/mf-002",
    "multi_fault/mf-003",
    # Named Replays (5)
    "named_replays/hist-cloudflare-2019",
    "named_replays/hist-aws-s3-2017",
    "named_replays/hist-github-2018",
    "named_replays/hist-datadog-2023",
    "named_replays/hist-discord-2022",
)

VAL_SPLIT: tuple[str, ...] = (
    # Single Fault (2)
    "single_fault/sf-006",
    "single_fault/sf-007",
    # Cascade (1)
    "cascade/cs-004",
    # Multi Fault (1)
    "multi_fault/mf-004",
    # Named Replays (2)
    "named_replays/hist-fastly-2021",
    "named_replays/hist-facebook-bgp-2021",
)

TEST_SPLIT: tuple[str, ...] = (
    # Single Fault (1)
    "single_fault/sf-008",
    # Cascade (1)
    "cascade/cs-005",
    # Multi Fault (1)
    "multi_fault/mf-005",
    # Named Replays (3)
    "named_replays/hist-slack-2022",
    "named_replays/hist-azure-dns-2019",
    "named_replays/hist-knight-capital-2012",
)

LEADERBOARD_SPLIT: tuple[str, ...] = (
    "single_fault/sf-001",
    "single_fault/sf-002",
    "single_fault/sf-006",
    "cascade/cs-001",
    "cascade/cs-002",
    "named_replays/hist-cloudflare-2019",
    "named_replays/hist-github-2018",
)

SPLIT_PARTITIONS: dict[str, tuple[str, ...]] = {
    "train": TRAIN_SPLIT,
    "val": VAL_SPLIT,
    "test": TEST_SPLIT,
    "leaderboard": LEADERBOARD_SPLIT,
}


# ── Integrity & Validation Helpers ────────────────────────────────────────────

def verify_split_disjointness() -> dict[str, Any]:
    """Verify that Train, Val, and Test splits are strictly pairwise disjoint and exhaustive."""
    train_set = set(TRAIN_SPLIT)
    val_set = set(VAL_SPLIT)
    test_set = set(TEST_SPLIT)
    all_catalog = set(SCENARIO_CATALOG.keys())

    train_val_overlap = train_set.intersection(val_set)
    train_test_overlap = train_set.intersection(test_set)
    val_test_overlap = val_set.intersection(test_set)
    union_set = train_set.union(val_set).union(test_set)
    missing = all_catalog - union_set
    extra = union_set - all_catalog

    is_disjoint = not (train_val_overlap or train_test_overlap or val_test_overlap)
    is_exhaustive = (union_set == all_catalog)

    return {
        "is_valid": is_disjoint and is_exhaustive,
        "is_disjoint": is_disjoint,
        "is_exhaustive": is_exhaustive,
        "train_count": len(TRAIN_SPLIT),
        "val_count": len(VAL_SPLIT),
        "test_count": len(TEST_SPLIT),
        "total_static_count": len(SCENARIO_CATALOG),
        "train_val_overlap": sorted(train_val_overlap),
        "train_test_overlap": sorted(train_test_overlap),
        "val_test_overlap": sorted(val_test_overlap),
        "missing_scenarios": sorted(missing),
        "extra_scenarios": sorted(extra),
    }


def verify_catalog_manifest_hashes(base_dir: Path | None = None) -> dict[str, Any]:
    """Verify on-disk manifest SHA-256 digests against the frozen scenario catalog."""
    base = base_dir or Path(__file__).resolve().parents[1]
    mismatches: dict[str, dict[str, str]] = {}
    missing_files: list[str] = []

    for sid, meta in SCENARIO_CATALOG.items():
        file_path = base / meta.manifest_relpath
        if not file_path.exists():
            missing_files.append(meta.manifest_relpath)
            continue
        actual_sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if actual_sha != meta.manifest_sha256:
            mismatches[sid] = {
                "expected": meta.manifest_sha256,
                "actual": actual_sha,
                "path": meta.manifest_relpath,
            }

    return {
        "is_valid": (len(missing_files) == 0 and len(mismatches) == 0),
        "total_verified": len(SCENARIO_CATALOG),
        "missing_files": missing_files,
        "mismatches": mismatches,
    }


def get_scenario(scenario_id: str) -> ScenarioMetadata:
    """Retrieve immutable ground-truth metadata for a scenario."""
    if scenario_id not in SCENARIO_CATALOG:
        raise KeyError(f"Unknown scenario_id: {scenario_id}. Must be one of {sorted(SCENARIO_CATALOG.keys())}")
    return SCENARIO_CATALOG[scenario_id]
