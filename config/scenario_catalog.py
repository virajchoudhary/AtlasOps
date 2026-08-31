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
        manifest_sha256="459282eb5bd46cbb5ce8e7ca29f0bd580f417ff38f6d3f6abea827cf5aaa6f66",
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
        manifest_sha256="23aac11f0faed1dd89b3e2428770483fd34b48a693681f4bea0badb757ff04b5",
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
        manifest_sha256="a2a300ec16af0039a4e1cbfe9dbcb3760093b20498ccc65079b0be65844b47da",
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
        manifest_sha256="2ab509499f78e1331d3137152b0baf278b5cca8097f9aa29d400176bf0d42962",
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
        manifest_sha256="f2c4e584fd3576586ac52a8bc59f386d6e7f58290cbcf2ffe758fc70491f9935",
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
        manifest_sha256="85739fb0a76713ef248724c975b75a526bddd531a3933c28bf434acee7d94bac",
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
        manifest_sha256="574f20851039f8b0b3f0bacd39d8b6dd1161dd41a9e0dcd41e972b1acc1c1a6a",
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
        manifest_sha256="3a6154f7f4332e2ffb73f6533a6892aff884765b2530c3c41054917684fdbac0",
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
        manifest_sha256="2b9f2f62c344265d38b4084aa3a54f79362ed4cdeb43e123fb9368e7047a5097",
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
        manifest_sha256="e9b7f230d8a91df39f1268f74a659bb096c4ef737560331db5bbad07e2abc7b4",
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
        manifest_sha256="dc1436090d3f2963ad776e90bf98c4884c4a2d78aed88c3572f8447c0b8a4331",
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
        manifest_sha256="b9e32cec2c6107155dcb8b456d1907bbe475e57fc111b6abd429289ce20809df",
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
        manifest_sha256="d023d3e625e9142d6e3f5c88fe98fb55bba35be8f3620c6ff47b477a10705a4e",
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
        manifest_sha256="109b70cd99f85290b2a7c82bee902dec49f9e0a3bf49ec92093a0d218aa1283c",
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
        manifest_sha256="16c0e89a3cf1cb2cc22190b2fcfb1b37a3e1b317d2c5f2aae579f73eb2670e89",
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
        manifest_sha256="19099bf079e48d210712741ec99a35717a686022e6001011093dece330a0ca4f",
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
        manifest_sha256="859f1595b0b01ba863b868fdbe028bcf874958ecc96fde8896b730f40808848b",
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
        manifest_sha256="8313a17905054aafe7018556b823d80713c62e4bbd55c526cbc84c879c13e00f",
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
        manifest_sha256="89f6863e75663cd38405d5bfbcacf78087ce064d15ac38b490e86f75fdb1cb46",
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
        manifest_sha256="b6f05820720541c33d1f87372ceca97b3de3c36b963e3743496a589912978ce1",
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
        manifest_sha256="4439215bb68d981cf5571b923521d010d7c325487b2fbeb023591424331514ca",
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
        manifest_sha256="099884e81506f3f71fcb5cce0eb43cf2abfd10447d881ea783029d3554e14786",
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
        manifest_sha256="0409e83a33b379209ce6748e8bc97b8ecb421c62d852b0365911df9d6dec06d2",
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
        manifest_sha256="7c7053d713789c333649b09aca1a64c74f62795672a0c0f40e4ba14fb599a59f",
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
        manifest_sha256="79e5c1d716c66cefbb5592190e67fabe709dc73cf0664967e8e118b545a21c3e",
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
        manifest_sha256="8e6c20873c17df3582283eec7189f3051d550f1773f227ec9c3bfc2472ec75e3",
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
        manifest_sha256="2daa8e366b4fb929af224d5c914e024ef8e2af5a4875d5bb9b467b6f1fa52a4a",
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
        manifest_sha256="51f88feabfcb59d08fcb1d4e54baa4d34739b14e9518838fb2f699027f50d0d5",
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
    """Verify on-disk manifest SHA-256 digests against the frozen scenario catalog.

    Normalizes byte content across Windows/Linux CRLF/LF variations to produce
    cross-platform deterministic cryptographic verification.
    """
    base = base_dir or Path(__file__).resolve().parents[1]
    mismatches: dict[str, dict[str, str]] = {}
    missing_files: list[str] = []

    for sid, meta in SCENARIO_CATALOG.items():
        file_path = base / meta.manifest_relpath
        if not file_path.exists():
            missing_files.append(meta.manifest_relpath)
            continue
        raw_bytes = file_path.read_bytes()
        normalized_bytes = raw_bytes.replace(b"\r\n", b"\n")
        actual_sha = hashlib.sha256(normalized_bytes).hexdigest()
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
