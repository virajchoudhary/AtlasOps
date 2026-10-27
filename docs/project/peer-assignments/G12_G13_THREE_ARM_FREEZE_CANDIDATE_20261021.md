# G12/G13 three-arm measurement — freeze sheet CANDIDATE, not approved

**Status:** prepared offline on 21 October 2026 for project-lead review. It binds the
[proposed G13 measurement freeze](../PROJECT_CLOSURE_EXECUTION_PROPOSAL_V1.md) and the
[common measurement contract v0.3 proposal](../G13_COMMON_MEASUREMENT_CONTRACT_V0_3_PROPOSAL.md)
to current source hashes and lists every unfrozen field. It authorizes nothing,
accesses no held-out data and invents no third arm. G12 remains
**IMPLEMENTED / EMPIRICAL EVIDENCE MISSING**; G13 remains **REOPENED / NOT_CERTIFIED**.

## Arms

| Arm | Identity | Status |
| --- | --- | --- |
| 1 Base | `Qwen/Qwen2.5-7B-Instruct` @ `a09a35458c702b33eeacc393d103063234e8bc28`, adapter disabled | identity known; serving attestation `UNSPECIFIED` |
| 2 Base + SFT-v17 | v17 manifest `7dd92122…1d5440`, weights `f20b5ae3…868fff` | identity known; serving attestation `UNSPECIFIED` |
| 3 SFT-v17 + accepted GRPO | **does not exist** — G9 NOT_PASSED/FROZEN | `UNSPECIFIED`; no surrogate permitted |

## Source and scorer hashes at `b23d6cb4319b77b4d8606934e401141bcb01d23f` (raw bytes, SHA-256)

| File | SHA-256 |
| --- | --- |
| `bench/candidate_measurement.py` | `ea26f06f78b18bae9d565af5fa0bf693d2b54d13e1acbb7a29d7a690bb046fe8` |
| `bench/candidate_replay.py` | `525b869265805a8e2b12ea6a793aabc4286bc782a719eb96f510a8af1e3183a3` |
| `bench/candidate_lineage.py` | `6c8e3cd5add2ec19eab9378ed90c9a300845c8d32f338a0569ba483ee77b6101` |
| `bench/candidate_adapters.py` | `b2f00f99ba0c5524c0157a4f8d46b89501bb65ab95394a803956c7fe5dea3e18` |
| `agents/verifier.py` | `7e040e63211af4b7bfb3c02f43ab16daccc325e8d8ea0bfd9625f61f8bbe8ae4` |
| `training/grpo_reward.py` (`score_direct_action_step`) | `95038a7faf338be6e348b393105d1ae10ceeeaa32f13d54fc9eab56170e27728` |
| `config/scenario_catalog.py` | `6d94db05f8d2e95622eb02a4363e9321e9fb75de78f91a51c2d8c04d956fefd4` |

These identify current source only; the campaign must re-pin at its frozen commit.

## Rules carried from the proposal (to be frozen verbatim, not re-derived)

- Denominator: all eligible started episodes, including malformed/no-output, policy/approval
  blocks and timeouts. Only independently adjudicated infrastructure-invalid episodes may be
  excluded, from an arm-, reward-, recovery- and TTR-blinded projection, counted separately.
- Recovery: authoritative `env_resolved=true`, conclusive verifier evidence, causal recovery
  before expiry and cleanup. Missing fields are null. Cleanup failures are reported, never deleted.
- TTR: from admitted Alertmanager delivery to first conclusive objective recovery before the
  deadline. Unresolved trials are censored at the last comparable pre-cleanup observation.
  Missing or incomparable clocks make TTR **null/unscorable**, never deadline-censored.
  A resolved-only mean is not a population claim.
- Metrics recomputed by an independent reviewer from raw event lines, not summaries.
- First campaign is descriptive; no superiority inference from one campaign.

## Unfrozen fields (owner decisions)

| Field | Owner | Value |
| --- | --- | --- |
| Accepted G9 checkpoint (arm 3) and G12 integrated-run permit | Project lead + operator | `UNSPECIFIED` |
| Clock source, precision, offset, wall/monotonic correlation, max skew, stale/out-of-order policy | Project lead + host owner | `UNSPECIFIED` |
| Population: Final-Test access (6 static Test scenarios exist in `TEST_SPLIT`; membership not read here) and adversarial membership/seed | Project lead | `UNSPECIFIED` |
| Seeds, balanced arm order, trial budget, reattempt authority | Project lead | `UNSPECIFIED` |
| Diagnosis taxonomy, no-output rule, per-step reward aggregation | Project lead + reviewer | `UNSPECIFIED` |
| Infrastructure-exclusion adjudicator and blinded projection schema | Independent reviewer | `UNSPECIFIED` |
| Serving attestation for each arm | Host owner + reviewer | `UNSPECIFIED` |

The Leaderboard split overlaps Train/Validation and is not an independent held-out population.
