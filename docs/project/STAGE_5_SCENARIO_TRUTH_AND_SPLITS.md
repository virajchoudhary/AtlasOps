# Stage 5: Freeze Scenario Truth and Benchmark Splits (Gate G5)

This governance and technical reference document freezes the complete scenario catalog, ground-truth root-cause definitions, objective verification predicates, cryptographic manifest hashes, and disjoint Train/Validation/Test benchmark splits for AtlasOps.

## 1. Governance & Leakage Prevention Rules

To ensure academic rigor and avoid data leakage between training, intermediate evaluation, and final held-out benchmarking:
1. **Split Disjointness**: The Train ($T_{\text{train}}$), Validation ($T_{\text{val}}$), and Held-Out Test ($T_{\text{test}}$) splits are strictly pairwise disjoint:
   $$T_{\text{train}} \cap T_{\text{val}} = \emptyset, \quad T_{\text{train}} \cap T_{\text{test}} = \emptyset, \quad T_{\text{val}} \cap T_{\text{test}} = \emptyset$$
2. **Catalog Exhaustiveness**: The union of all partitions equals the full 28 frozen static scenarios:
   $$T_{\text{train}} \cup T_{\text{val}} \cup T_{\text{test}} = S_{28}$$
3. **Multi-Tier Representation**: Every tier (`single_fault`, `cascade`, `multi_fault`, `named_replays`) is represented in all three splits.
4. **Test Set Isolation**: Trajectory generation (Stage 7), Supervised Fine-Tuning (Stage 8), and GRPO Policy Training (Stage 9) are strictly confined to $T_{\text{train}}$. The test split $T_{\text{test}}$ is never exposed during training.

---

## 2. Benchmark Split Partitions

### Summary Partition Matrix

| Split | Single Fault | Cascade | Multi Fault | Named Replays | Total Scenarios | Purpose / Scope |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **Train** | 5 | 3 | 3 | 5 | **16** | SFT trajectory generation & GRPO exploration curriculum |
| **Validation** | 2 | 1 | 1 | 2 | **6** | Hyperparameter tuning, checkpoint selection & sanity eval |
| **Held-Out Test**| 1 | 1 | 1 | 3 | **6** | Zero-shot baseline (Stage 6) & final un-leaked evaluation (Stage 13) |
| **Total Static** | **8** | **5** | **5** | **10** | **28** | **Full Frozen Catalogue** |
| *Leaderboard* | 3 | 2 | 0 | 2 | *7* | Compact rapid benchmarking subset |

---

## 3. Detailed Partition Breakdown

### A. Train Split ($|T_{\text{train}}| = 16$, Seed = `2026`)
- `single_fault/sf-001` (PodChaos cartservice kill)
- `single_fault/sf-002` (StressChaos paymentservice CPU)
- `single_fault/sf-003` (StressChaos checkoutservice memory)
- `single_fault/sf-004` (NetworkChaos frontend packet loss)
- `single_fault/sf-005` (NetworkChaos redis-cart partition)
- `cascade/cs-001` (NetworkChaos currencyservice latency)
- `cascade/cs-002` (NetworkChaos redis-cart partition)
- `cascade/cs-003` (StressChaos recommendationservice CPU)
- `multi_fault/mf-001` (NetworkChaos frontend loss + StressChaos checkoutservice CPU)
- `multi_fault/mf-002` (NetworkChaos redis-cart partition + StressChaos recommendationservice memory)
- `multi_fault/mf-003` (DNSChaos cluster drop + NetworkChaos currencyservice delay)
- `named_replays/hist-cloudflare-2019` (StressChaos frontend 100% CPU)
- `named_replays/hist-aws-s3-2017` (Application productcatalogservice replica 0)
- `named_replays/hist-github-2018` (PodChaos redis-cart primary kill)
- `named_replays/hist-datadog-2023` (DNSChaos systemd-resolved failure)
- `named_replays/hist-discord-2022` (PodChaos redis-cart kill + NetworkChaos cartservice latency)

### B. Validation Split ($|T_{\text{val}}| = 6$, Seed = `1337`)
- `single_fault/sf-006` (DNSChaos checkoutservice random lookup failure)
- `single_fault/sf-007` (IOChaos emailservice disk full / IO error)
- `cascade/cs-004` (IOChaos emailservice disk write exhaustion)
- `multi_fault/mf-004` (TimeChaos paymentservice clock skew + NetworkChaos cartservice corrupt)
- `named_replays/hist-fastly-2021` (NetworkChaos frontend corrupt 60%)
- `named_replays/hist-facebook-bgp-2021` (NetworkChaos default $\rightarrow$ kube-system partition)

### C. Held-Out Test Split ($|T_{\text{test}}| = 6$, Seed = `42`)
- `single_fault/sf-008` (TimeChaos paymentservice clock skew)
- `cascade/cs-005` (NetworkChaos cartservice latency + StressChaos cartservice memory)
- `multi_fault/mf-005` (IOChaos emailservice disk fault + NetworkChaos checkoutservice delay)
- `named_replays/hist-slack-2022` (NetworkChaos frontend duplicate + checkoutservice delay)
- `named_replays/hist-azure-dns-2019` (DNSChaos random lookup failure across mesh)
- `named_replays/hist-knight-capital-2012` (Deployment checkoutservice-legacy dead code)

---

## 4. Complete Cryptographic Manifest Digest Registry (Canonical LF)

| Scenario ID | Tier | Kinds | SHA-256 Digest |
| :--- | :--- | :--- | :--- |
| `single_fault/sf-001` | `single_fault` | `PodChaos` | `459282eb5bd46cbb5ce8e7ca29f0bd580f417ff38f6d3f6abea827cf5aaa6f66` |
| `single_fault/sf-002` | `single_fault` | `StressChaos` | `23aac11f0faed1dd89b3e2428770483fd34b48a693681f4bea0badb757ff04b5` |
| `single_fault/sf-003` | `single_fault` | `StressChaos` | `a2a300ec16af0039a4e1cbfe9dbcb3760093b20498ccc65079b0be65844b47da` |
| `single_fault/sf-004` | `single_fault` | `NetworkChaos` | `2ab509499f78e1331d3137152b0baf278b5cca8097f9aa29d400176bf0d42962` |
| `single_fault/sf-005` | `single_fault` | `NetworkChaos` | `f2c4e584fd3576586ac52a8bc59f386d6e7f58290cbcf2ffe758fc70491f9935` |
| `single_fault/sf-006` | `single_fault` | `DNSChaos` | `85739fb0a76713ef248724c975b75a526bddd531a3933c28bf434acee7d94bac` |
| `single_fault/sf-007` | `single_fault` | `IOChaos` | `574f20851039f8b0b3f0bacd39d8b6dd1161dd41a9e0dcd41e972b1acc1c1a6a` |
| `single_fault/sf-008` | `single_fault` | `TimeChaos` | `3a6154f7f4332e2ffb73f6533a6892aff884765b2530c3c41054917684fdbac0` |
| `cascade/cs-001` | `cascade` | `NetworkChaos` | `2b9f2f62c344265d38b4084aa3a54f79362ed4cdeb43e123fb9368e7047a5097` |
| `cascade/cs-002` | `cascade` | `NetworkChaos` | `e9b7f230d8a91df39f1268f74a659bb096c4ef737560331db5bbad07e2abc7b4` |
| `cascade/cs-003` | `cascade` | `StressChaos` | `dc1436090d3f2963ad776e90bf98c4884c4a2d78aed88c3572f8447c0b8a4331` |
| `cascade/cs-004` | `cascade` | `IOChaos` | `b9e32cec2c6107155dcb8b456d1907bbe475e57fc111b6abd429289ce20809df` |
| `cascade/cs-005` | `cascade` | `NetworkChaos`, `StressChaos` | `d023d3e625e9142d6e3f5c88fe98fb55bba35be8f3620c6ff47b477a10705a4e` |
| `multi_fault/mf-001` | `multi_fault` | `NetworkChaos`, `StressChaos` | `109b70cd99f85290b2a7c82bee902dec49f9e0a3bf49ec92093a0d218aa1283c` |
| `multi_fault/mf-002` | `multi_fault` | `NetworkChaos`, `StressChaos` | `16c0e89a3cf1cb2cc22190b2fcfb1b37a3e1b317d2c5f2aae579f73eb2670e89` |
| `multi_fault/mf-003` | `multi_fault` | `DNSChaos`, `NetworkChaos` | `19099bf079e48d210712741ec99a35717a686022e6001011093dece330a0ca4f` |
| `multi_fault/mf-004` | `multi_fault` | `TimeChaos`, `NetworkChaos` | `859f1595b0b01ba863b868fdbe028bcf874958ecc96fde8896b730f40808848b` |
| `multi_fault/mf-005` | `multi_fault` | `IOChaos`, `NetworkChaos` | `8313a17905054aafe7018556b823d80713c62e4bbd55c526cbc84c879c13e00f` |
| `named_replays/hist-aws-s3-2017` | `named_replays` | `Application` | `89f6863e75663cd38405d5bfbcacf78087ce064d15ac38b490e86f75fdb1cb46` |
| `named_replays/hist-azure-dns-2019` | `named_replays` | `DNSChaos` | `b6f05820720541c33d1f87372ceca97b3de3c36b963e3743496a589912978ce1` |
| `named_replays/hist-cloudflare-2019` | `named_replays` | `StressChaos` | `4439215bb68d981cf5571b923521d010d7c325487b2fbeb023591424331514ca` |
| `named_replays/hist-datadog-2023` | `named_replays` | `DNSChaos` | `099884e81506f3f71fcb5cce0eb43cf2abfd10447d881ea783029d3554e14786` |
| `named_replays/hist-discord-2022` | `named_replays` | `PodChaos`, `NetworkChaos` | `0409e83a33b379209ce6748e8bc97b8ecb421c62d852b0365911df9d6dec06d2` |
| `named_replays/hist-facebook-bgp-2021` | `named_replays` | `NetworkChaos` | `7c7053d713789c333649b09aca1a64c74f62795672a0c0f40e4ba14fb599a59f` |
| `named_replays/hist-fastly-2021` | `named_replays` | `NetworkChaos` | `79e5c1d716c66cefbb5592190e67fabe709dc73cf0664967e8e118b545a21c3e` |
| `named_replays/hist-github-2018` | `named_replays` | `PodChaos` | `8e6c20873c17df3582283eec7189f3051d550f1773f227ec9c3bfc2472ec75e3` |
| `named_replays/hist-knight-capital-2012` | `named_replays` | `Deployment` | `2daa8e366b4fb929af224d5c914e024ef8e2af5a4875d5bb9b467b6f1fa52a4a` |
| `named_replays/hist-slack-2022` | `named_replays` | `NetworkChaos` | `51f88feabfcb59d08fcb1d4e54baa4d34739b14e9518838fb2f699027f50d0d5` |

---

## 5. Gate G5 Verification Evidence

Gate G5 is formally verified by automated unit tests in `tests/test_stage5_scenario_splits_and_truth.py`:
- `test_all_28_manifests_exist_and_hashes_match`: **PASS** (100% manifest digest match).
- `test_split_disjointness_and_exhaustiveness`: **PASS** (zero overlap, 100% catalog coverage).
- `test_verifier_coverage_for_all_scenarios`: **PASS** (all 28 specs present in `agents/verifier.py`).
- `test_service_catalog_compliance`: **PASS** (all referenced services in Online Boutique catalog).
- `test_tier_distribution_across_splits`: **PASS** (4/4 tiers present across all splits).

**Gate G5 Status**: **`PASS`**
