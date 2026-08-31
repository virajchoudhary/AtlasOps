# Stage 5: Freeze Scenario Truth and Benchmark Splits (Gate G5)

This governance and technical reference document freezes the complete scenario catalog, ground-truth root-cause definitions, objective verification predicates, cryptographic manifest hashes, and disjoint Train/Validation/Test benchmark splits for AtlasOps.

## 1. Governance & Leakage Prevention Rules

To ensure academic rigor and avoid data leakage between training, intermediate evaluation, and final held-out benchmarking:
1. **Split Disjointness**: The Train ($T_{\\text{train}}$), Validation ($T_{\\text{val}}$), and Held-Out Test ($T_{\\text{test}}$) splits are strictly pairwise disjoint:
   $$T_{\\text{train}} \\cap T_{\\text{val}} = \\emptyset, \\quad T_{\\text{train}} \\cap T_{\\text{test}} = \\emptyset, \\quad T_{\\text{val}} \\cap T_{\\text{test}} = \\emptyset$$
2. **Catalog Exhaustiveness**: The union of all partitions equals the full 28 frozen static scenarios:
   $$T_{\\text{train}} \\cup T_{\\text{val}} \\cup T_{\\text{test}} = S_{28}$$
3. **Multi-Tier Representation**: Every tier (`single_fault`, `cascade`, `multi_fault`, `named_replays`) is represented in all three splits.
4. **Test Set Isolation**: Trajectory generation (Stage 7), Supervised Fine-Tuning (Stage 8), and GRPO Policy Training (Stage 9) are strictly confined to $T_{\\text{train}}$. The test split $T_{\\text{test}}$ is never exposed during training.

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

### A. Train Split ($|T_{\\text{train}}| = 16$, Seed = `2026`)
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

### B. Validation Split ($|T_{\\text{val}}| = 6$, Seed = `1337`)
- `single_fault/sf-006` (DNSChaos checkoutservice random lookup failure)
- `single_fault/sf-007` (IOChaos emailservice disk full / IO error)
- `cascade/cs-004` (IOChaos emailservice disk write exhaustion)
- `multi_fault/mf-004` (TimeChaos paymentservice clock skew + NetworkChaos cartservice corrupt)
- `named_replays/hist-fastly-2021` (NetworkChaos frontend corrupt 60%)
- `named_replays/hist-facebook-bgp-2021` (NetworkChaos default $\\rightarrow$ kube-system partition)

### C. Held-Out Test Split ($|T_{\\text{test}}| = 6$, Seed = `42`)
- `single_fault/sf-008` (TimeChaos paymentservice clock skew)
- `cascade/cs-005` (NetworkChaos cartservice latency + StressChaos cartservice memory)
- `multi_fault/mf-005` (IOChaos emailservice disk fault + NetworkChaos checkoutservice delay)
- `named_replays/hist-slack-2022` (NetworkChaos frontend duplicate + checkoutservice delay)
- `named_replays/hist-azure-dns-2019` (DNSChaos random lookup failure across mesh)
- `named_replays/hist-knight-capital-2012` (Deployment checkoutservice-legacy dead code)

---

## 4. Complete Cryptographic Manifest Digest Registry

| Scenario ID | Tier | Kinds | SHA-256 Digest |
| :--- | :--- | :--- | :--- |
| `single_fault/sf-001` | `single_fault` | `PodChaos` | `90c402c4f88465c13954531fdc5019788a98de8137ea3c157d231d448519800b` |
| `single_fault/sf-002` | `single_fault` | `StressChaos` | `06686ac0645bd039af56ae1fb0dfd787f8010c8086d524e3b92406180f9575e7` |
| `single_fault/sf-003` | `single_fault` | `StressChaos` | `4dfb91c8f36f19cc219df35b1d62a70c0984c8c9a809d326a6c78ef424968dde` |
| `single_fault/sf-004` | `single_fault` | `NetworkChaos` | `b78f35d8562ef6414d5243378079110fe00b2bc5e5fff7f0e67a1b342bafa918` |
| `single_fault/sf-005` | `single_fault` | `NetworkChaos` | `0b896f22557f1319e2bab7f7f2db5ff02c89754fe545b81de2088444ad270046` |
| `single_fault/sf-006` | `single_fault` | `DNSChaos` | `886e4873b3f71761327751c41f7250202b0dda6c8a78e02536c6df0f526e8b28` |
| `single_fault/sf-007` | `single_fault` | `IOChaos` | `3e15a122b34aed52a4cee16f315606b7cd4058acdd05019777c9ec65fa3e2c06` |
| `single_fault/sf-008` | `single_fault` | `TimeChaos` | `41d3f6d2ed0278bbc101740f738575bcd5eccf97e8971b17308e39e3dab1ac3b` |
| `cascade/cs-001` | `cascade` | `NetworkChaos` | `6b777ce506cde061236cf340db78074d29487be9ae4ed32d8a06b169f1e153a7` |
| `cascade/cs-002` | `cascade` | `NetworkChaos` | `05e36fdd8c48e0d81148ce233fb86755fc4b2e53a3964e6ae8e583af742183c7` |
| `cascade/cs-003` | `cascade` | `StressChaos` | `7301692096c221919e1a005341cb8348052186016f709441d35471321d7c1771` |
| `cascade/cs-004` | `cascade` | `IOChaos` | `7b5a11f3a4f679d2cde7decf6a92e32dbfea4f4fb13d60831ef091257b2fec79` |
| `cascade/cs-005` | `cascade` | `NetworkChaos`, `StressChaos` | `448df63030582a2390360b1064bbc81c529b7f6060d803a02606e72330e261b5` |
| `multi_fault/mf-001` | `multi_fault` | `NetworkChaos`, `StressChaos` | `1e2b6a68107dfbec9c3ee4c524a82aef6deb5eceb42d122f4bba4b451c49a852` |
| `multi_fault/mf-002` | `multi_fault` | `NetworkChaos`, `StressChaos` | `1a656644a99d8da25f26bfa6030359b84658d2e921fecf9126226516b5741334` |
| `multi_fault/mf-003` | `multi_fault` | `DNSChaos`, `NetworkChaos` | `447ac4edf080f2ad141f3ff7bcf8b60420f31d6117eae767e5d0cd776103221e` |
| `multi_fault/mf-004` | `multi_fault` | `TimeChaos`, `NetworkChaos` | `0eff6068d2c5813d2d35770c288d1b65bd0f728d4a561769c7a387a3b73ad7c4` |
| `multi_fault/mf-005` | `multi_fault` | `IOChaos`, `NetworkChaos` | `959048e58473ffe29d84a32fee9902d98e0a36f7c0db867932210e8dfbbbc968` |
| `named_replays/hist-aws-s3-2017` | `named_replays` | `Application` | `55e3babadc529686e872f9d3800304edda3f4ad9ec55e7e25be7ad2c8ca69c78` |
| `named_replays/hist-azure-dns-2019` | `named_replays` | `DNSChaos` | `fa78984d8e099db6e3c20cbbc685d0a26d1cf99be3512fd0b607e892059a5db4` |
| `named_replays/hist-cloudflare-2019` | `named_replays` | `StressChaos` | `f85f73d68869c2256fd2340017470380e62ff170d899bc2e62d13c1a22d70490` |
| `named_replays/hist-datadog-2023` | `named_replays` | `DNSChaos` | `cfa781546a1953f00e500ef79465870596542c6f7e584970471a070ea36ecda2` |
| `named_replays/hist-discord-2022` | `named_replays` | `PodChaos`, `NetworkChaos` | `e74f931dc0c70872bfd5f96d35c27e9263fd19c9b4c7d6e477a4e5bc368d9741` |
| `named_replays/hist-facebook-bgp-2021` | `named_replays` | `NetworkChaos` | `4a99827df3a0402f2a3e8e5038caebd588d350dcb70fdd1b13de98f3ccf46254` |
| `named_replays/hist-fastly-2021` | `named_replays` | `NetworkChaos` | `957335d19cbd4a955362fedb7a7ae8454d4a9618c9b175740a7c19594f309735` |
| `named_replays/hist-github-2018` | `named_replays` | `PodChaos` | `72570683734d6165b3f1d755c928a810cd4109ac9946ace7900f2434954e6a62` |
| `named_replays/hist-knight-capital-2012` | `named_replays` | `Deployment` | `45fd37cd50720c407590679005c7131965398e5c89b1f01ce031e5d51444e069` |
| `named_replays/hist-slack-2022` | `named_replays` | `NetworkChaos` | `ab6f396bff921f960ae7ab12350c3241847f05e54a494378e123bfab08e6e5d4` |

---

## 5. Gate G5 Verification Evidence

Gate G5 is formally verified by automated unit tests in `tests/test_stage5_scenario_splits_and_truth.py`:
- `test_all_28_manifests_exist_and_hashes_match`: **PASS** (100% manifest digest match).
- `test_split_disjointness_and_exhaustiveness`: **PASS** (zero overlap, 100% catalog coverage).
- `test_verifier_coverage_for_all_scenarios`: **PASS** (all 28 specs present in `agents/verifier.py`).
- `test_service_catalog_compliance`: **PASS** (all referenced services in Online Boutique catalog).
- `test_tier_distribution_across_splits`: **PASS** (4/4 tiers present across all splits).

**Gate G5 Status**: **`PASS`**
