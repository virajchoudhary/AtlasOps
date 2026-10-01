# AtlasOps — Submission Readiness and Asset Inventory

- **Project Repository**: `virajchoudhary/AtlasOps`
- **Upstream Baseline**: `Harikishanth/AtlasOps @ bf9bd19`
- **Working Pipeline**: `v2.2` with [GAI + RL (RS optional historical research)](../docs/project/GAI_RL_SCOPE_REVISION.md); Section 25 and the measurement protocol are not frozen
- **Certification**: **NOT_CERTIFIED**
- **Generated**: `2026-10-01T00:18:00.818088+00:00`
- **Gate inventory source**: `docs/project/MASTER_PIPELINE_STATUS.md`

## Declared Gate Statuses

| Gate | Status |
| :--- | :--- |
| G0 | PASS |
| G1 | PASS |
| G2 | PASS |
| G3 | PASS |
| G4 | NOT_PASSED |
| G5 | PASS |
| G6 | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING |
| G7 | PARTIAL |
| G8 | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING |
| G9 | REOPENED |
| G10 | OUT_OF_SCOPE |
| G11 | OUT_OF_SCOPE |
| G12 | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING |
| G13 | REOPENED |
| G14 | PARTIAL |
| G15 | PARTIAL |

G3 PASS is historical local Kind acceptance with wrapper and tracing caveats; it does not establish current cluster health.
G10/G11 OUT_OF_SCOPE retain historical scenario-derived RS evidence; the former bounded G11 PASS was not real incident improvement.

No full-pipeline empirical metric is certified by this asset inventory.
Asset hashes establish file integrity, not scientific gate closure.

## Canonical Submission Artifacts

| Asset Path | SHA-256 Digest | Size (Bytes) |
| :--- | :--- | :---: |
| `.gitattributes` | `d2be61018599d3dd...` | 1753 |
| `.github/workflows/ci.yml` | `2e76e97d41e87a5e...` | 2083 |
| `AGENTS.md` | `fec2b380e8dc8d27...` | 6416 |
| `BENCHMARKS.md` | `469f977169a3cd56...` | 4549 |
| `BLOG.md` | `1c566e34d9a0b605...` | 13153 |
| `Makefile` | `34bbfe03619f49b1...` | 4129 |
| `README.md` | `6948f357086a2433...` | 25502 |
| `agents/_http_retry.py` | `4af34e93b62324e6...` | 9380 |
| `agents/adversarial_designer.py` | `f6bc96059e6ca56a...` | 21393 |
| `agents/approval.py` | `f8fd1975c2648f20...` | 9781 |
| `agents/approval_http.py` | `60e3fb912a9b5c4b...` | 2990 |
| `agents/coordinator.py` | `5d7be471592526fb...` | 118065 |
| `agents/grounding.py` | `c228e7a4bce97f74...` | 11780 |
| `agents/judge.py` | `b817d1433d43e70a...` | 7853 |
| `agents/policy_remediation.py` | `d139b005221f27df...` | 5977 |
| `agents/prompts/comms.md` | `be58db4dcef9b422...` | 2662 |
| `agents/prompts/diagnosis.md` | `26265a2007477eed...` | 2902 |
| `agents/prompts/remediation.md` | `00d0d3b24ba00007...` | 4768 |
| `agents/prompts/triage.md` | `956a1489758cb0fe...` | 2181 |
| `agents/tool_policy.py` | `c7c755b1fa33c9b7...` | 2884 |
| `agents/tools/argocd.py` | `5cfdad11258fcf68...` | 10784 |
| `agents/tools/chaos.py` | `089c5dec071ecac7...` | 12540 |
| `agents/tools/prometheus.py` | `dc8d87332f66dae8...` | 3586 |
| `agents/verifier.py` | `850dc5f2f197887e...` | 37725 |
| `app.py` | `5118800d6f05f244...` | 17568 |
| `artifacts/evidence/.gitattributes` | `a5c3211d796d1d08...` | 880 |
| `artifacts/evidence/recovery/2026-09-05-workspace-recovery.json` | `710fddcaeb2ea6c8...` | 22788 |
| `artifacts/evidence/recovery/SETUP-03_COMMANDS.md` | `0819c3ffe5e9cfe6...` | 15741 |
| `artifacts/evidence/stage10/rs_baseline_eval.json` | `821c76fc6958593b...` | 4130 |
| `artifacts/evidence/stage10/rs_dataset_manifest.json` | `69d7bfa78cccda00...` | 587 |
| `artifacts/evidence/stage11/rs_hybrid_eval.json` | `2ff967237a7bf21c...` | 3234 |
| `artifacts/evidence/stage11/rs_hybrid_eval_synthetic_v2.json` | `fe32b345c8bcddf3...` | 4544 |
| `artifacts/evidence/stage13/ablation_benchmark_results.json` | `3b7f1f88e6e37a96...` | 7156 |
| `artifacts/evidence/stage3/acceptance_report.json` | `cbf33ff804e699ea...` | 10099 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-002.json` | `082d87065a3b3208...` | 7924 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-003.json` | `28719b806d9e97c2...` | 8406 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-004.cleanup.json` | `3efc65bd84398007...` | 421 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-004.json` | `7a8b25aa8601a880...` | 7486 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-004.runlog.txt` | `d55117e07fb3f216...` | 4574 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-005.cleanup.json` | `2cad59217205ac3e...` | 323 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-005.json` | `f174cba840a72bcb...` | 7860 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-005.runlog.txt` | `39b079f59ebc3f12...` | 871 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-006.runlog.txt` | `269c8b2cdf402a08...` | 652 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-007.cleanup.json` | `e07d9a0600806487...` | 323 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-007.json` | `a562cf84d9207aeb...` | 7810 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-007.runlog.txt` | `b2e196fc84b50cd6...` | 1033 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-008.cleanup.json` | `258086ce95931726...` | 423 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-008.json` | `79d45c7a22fa5509...` | 31303 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-008.runlog.txt` | `7af7999074fc30be...` | 9698 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-009.cleanup.json` | `dfe8f4fbf5cb13af...` | 613 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-009.interruption.json` | `ff23c36e8bb96bfc...` | 2158 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-009.leftover-chaos.yaml` | `1285e7e175eba956...` | 4145 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-010.cleanup.json` | `0ab5c3251e550925...` | 423 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-010.json` | `a405a33b379cfb12...` | 754655 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-011.cleanup.json` | `f3687e70173da647...` | 617 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-011.interruption.json` | `d41eb45edc07169d...` | 1750 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-011.leftover-chaos.yaml` | `6fdd5f42893b4b1d...` | 1609 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-012.cleanup.json` | `240b747d453fb6ae...` | 496 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-012.interruption.json` | `3e9ca8a1ff31c52d...` | 1233 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-013.cleanup.json` | `e1e17c2aa2be4abf...` | 496 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-013.interruption.json` | `77dadcb96e26f3d3...` | 1233 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-014.cleanup.json` | `e8c2b8771a0c06fd...` | 496 |
| `artifacts/evidence/stage4/EXP-STAGE4-SF002-014.interruption.json` | `da24200124f0b92e...` | 1233 |
| `artifacts/evidence/stage4/RECOVERY_INDEX_009_014.md` | `a6e910f350bd1a4e...` | 2334 |
| `artifacts/evidence/stage4/golden_incident_sf002_manifest.json` | `7a8b25aa8601a880...` | 7486 |
| `artifacts/evidence/stage7/candidates/train-candidate-v1/README.md` | `60ccf39b0eff611d...` | 2331 |
| `artifacts/evidence/stage7/candidates/train-candidate-v1/quality_audit.md` | `94bc9d662cd443f5...` | 7194 |
| `artifacts/evidence/stage7/candidates/train-candidate-v1/sft_corpus_manifest.json` | `35c9fd63328ef131...` | 7667 |
| `artifacts/evidence/stage7/candidates/train-candidate-v1/sft_corpus_train.jsonl` | `19606e4fec300f64...` | 655065 |
| `artifacts/evidence/stage7/qwen_a09a354_weight_metadata_v1.json` | `32159b8e77f3ad62...` | 1073 |
| `artifacts/evidence/stage7/sft_corpus_manifest.json` | `c3527d6e25069a21...` | 704 |
| `artifacts/evidence/stage7/sft_tokenizer_preflight_2048_negative_v1.json` | `a3ef2e188a765687...` | 99134 |
| `artifacts/evidence/stage7/sft_tokenizer_preflight_2048_negative_v2.json` | `e5c06fdc122ec783...` | 81930 |
| `artifacts/evidence/stage7/sft_tokenizer_preflight_v1.json` | `bd55ec4d962e82ca...` | 81243 |
| `artifacts/evidence/stage7/sft_tokenizer_preflight_v2.json` | `d0283fd2404540cf...` | 81243 |
| `artifacts/evidence/stage7/sft_tokenizer_preflight_v3.json` | `4833517a7e0f9b78...` | 81243 |
| `artifacts/evidence/stage7/sft_tokenizer_preflight_v4.json` | `64bd54a2f0e15ad9...` | 81360 |
| `artifacts/evidence/stage7/sft_tokenizer_preflight_v5.json` | `9eccf0353af2e605...` | 82799 |
| `artifacts/evidence/stage7/sft_training_config.json` | `be26026069c5f272...` | 583 |
| `artifacts/evidence/stage7/tokenizer_files_a09a354_v1.json` | `297724616fe6bdd1...` | 1358 |
| `artifacts/models/hybrid_recommender.json` | `53d0a4fb640b4691...` | 2646 |
| `artifacts/models/hybrid_recommender_synthetic_v2.json` | `21243d035fc2a972...` | 2192 |
| `artifacts/overnight_experiments/rs-20260924/interactions.jsonl` | `4e60a94564124bb9...` | 8750 |
| `artifacts/overnight_experiments/rs-20260924/interactions.manifest.json` | `57dfd45e856bf8f2...` | 9329 |
| `bench/ablation_suite.py` | `825866a09a865725...` | 26652 |
| `bench/candidate_adapters.py` | `b2f00f99ba0c5524...` | 31103 |
| `bench/candidate_lineage.py` | `396ff6d389f2b836...` | 28333 |
| `bench/candidate_measurement.py` | `920e73560ddd162a...` | 38684 |
| `bench/candidate_replay.py` | `7c418ff0ee34663f...` | 50927 |
| `bench/chaos_manifests/cascade/cs-001.yaml` | `6b777ce506cde061...` | 566 |
| `bench/chaos_manifests/cascade/cs-002.yaml` | `05e36fdd8c48e0d8...` | 491 |
| `bench/chaos_manifests/cascade/cs-003.yaml` | `7301692096c22191...` | 452 |
| `bench/chaos_manifests/cascade/cs-004.yaml` | `7b5a11f3a4f679d2...` | 469 |
| `bench/chaos_manifests/cascade/cs-005.yaml` | `448df63030582a23...` | 978 |
| `bench/chaos_manifests/multi_fault/mf-001.yaml` | `1e2b6a68107dfbec...` | 788 |
| `bench/chaos_manifests/multi_fault/mf-002.yaml` | `1a656644a99d8da2...` | 907 |
| `bench/chaos_manifests/multi_fault/mf-003.yaml` | `447ac4edf080f2ad...` | 785 |
| `bench/chaos_manifests/multi_fault/mf-004.yaml` | `0eff6068d2c5813d...` | 765 |
| `bench/chaos_manifests/multi_fault/mf-005.yaml` | `959048e58473ffe2...` | 844 |
| `bench/chaos_manifests/named_replays/hist-aws-s3-2017.yaml` | `55e3babadc529686...` | 985 |
| `bench/chaos_manifests/named_replays/hist-azure-dns-2019.yaml` | `fa78984d8e099db6...` | 632 |
| `bench/chaos_manifests/named_replays/hist-cloudflare-2019.yaml` | `f85f73d68869c225...` | 639 |
| `bench/chaos_manifests/named_replays/hist-datadog-2023.yaml` | `cfa781546a1953f0...` | 504 |
| `bench/chaos_manifests/named_replays/hist-discord-2022.yaml` | `e74f931dc0c70872...` | 981 |
| `bench/chaos_manifests/named_replays/hist-facebook-bgp-2021.yaml` | `4a99827df3a0402f...` | 655 |
| `bench/chaos_manifests/named_replays/hist-fastly-2021.yaml` | `957335d19cbd4a95...` | 555 |
| `bench/chaos_manifests/named_replays/hist-github-2018.yaml` | `72570683734d6165...` | 594 |
| `bench/chaos_manifests/named_replays/hist-knight-capital-2012.yaml` | `45fd37cd50720c40...` | 1480 |
| `bench/chaos_manifests/named_replays/hist-slack-2022.yaml` | `ab6f396bff921f96...` | 1034 |
| `bench/chaos_manifests/single_fault/sf-001.yaml` | `90c402c4f88465c1...` | 304 |
| `bench/chaos_manifests/single_fault/sf-002.yaml` | `06686ac0645bd039...` | 369 |
| `bench/chaos_manifests/single_fault/sf-003.yaml` | `4dfb91c8f36f19cc...` | 382 |
| `bench/chaos_manifests/single_fault/sf-004.yaml` | `b78f35d8562ef641...` | 373 |
| `bench/chaos_manifests/single_fault/sf-005.yaml` | `0b896f22557f1319...` | 476 |
| `bench/chaos_manifests/single_fault/sf-006.yaml` | `886e4873b3f71761...` | 393 |
| `bench/chaos_manifests/single_fault/sf-007.yaml` | `3e15a122b34aed52...` | 440 |
| `bench/chaos_manifests/single_fault/sf-008.yaml` | `41d3f6d2ed0278bb...` | 388 |
| `bench/episode_membership.py` | `c598ab421638db9c...` | 26114 |
| `bench/grpo_eval.py` | `b819d0608071b617...` | 67772 |
| `bench/runner.py` | `6e75db82c21d1ad5...` | 15551 |
| `bench/sft_eval.py` | `9815fce6a8d24506...` | 41843 |
| `bench/zero_shot_baseline.py` | `8ae21585a8c3d446...` | 54439 |
| `config/g4_protocol.py` | `676227c8326e0526...` | 25141 |
| `config/runtime.py` | `96fe1d1834846cb3...` | 18567 |
| `config/scenario_catalog.py` | `6d94db05f8d2e956...` | 27577 |
| `config/sft_pilot_v1.json` | `3e768a5c8eaa1e77...` | 4341 |
| `config/sft_pilot_v2.json` | `77f58492b4d892ab...` | 4341 |
| `config/sft_pilot_v3.json` | `a227ea6e45c0299e...` | 4341 |
| `config/sft_pilot_v4.json` | `914f7a5af5c22a35...` | 4341 |
| `config/sft_remote_host_proposal_v1.json` | `07c476d9d378836e...` | 2203 |
| `config/splits.py` | `31c270e9596f9c35...` | 1421 |
| `dashboard.py` | `7e4afc02551bf4d6...` | 23653 |
| `demo/launcher.py` | `a9d51bc21b7e45dc...` | 1744 |
| `docs/AtlasOps_Technical_Report.md` | `d6a72d703dd29612...` | 8400 |
| `docs/BENCHMARKS.md` | `f4054130730f4981...` | 2831 |
| `docs/END_TO_END_FLOW.md` | `3e594773cf15c6b7...` | 5629 |
| `docs/EXPERIMENT_REGISTRY.md` | `ed16f1e24448df98...` | 15637 |
| `docs/HF_SPACE_SETUP.md` | `b9fd2597fb62d620...` | 8350 |
| `docs/media/console-overview-20260926.png` | `4e9d204e953ee2f9...` | 101431 |
| `docs/media/gradio-demo-20260926.png` | `399c06bad7c2dca0...` | 53821 |
| `docs/project/DYNAMIC_ADVERSARIAL_PROPOSAL_CONTRACT.md` | `60653270d21b9971...` | 3301 |
| `docs/project/FINAL_PIPELINE_V22_STATUS.md` | `b94ffd4cf482dc6b...` | 9462 |
| `docs/project/G13_COMMON_MEASUREMENT_CONTRACT_V0_3_PROPOSAL.md` | `66e442b7b7ff95c5...` | 12133 |
| `docs/project/G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_1.md` | `613bbdfe304225f0...` | 9884 |
| `docs/project/G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_2.md` | `0545315a6d24349a...` | 6939 |
| `docs/project/G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_3.md` | `63529cd8a08ca3ba...` | 4806 |
| `docs/project/G13_THREE_ARM_RAW_REPLAY_CANDIDATE_V0_1.md` | `c94fb7146a9f89a9...` | 5918 |
| `docs/project/G13_THREE_ARM_RAW_REPLAY_CANDIDATE_V0_2.md` | `f649ce38db3345ca...` | 3117 |
| `docs/project/G13_THREE_ARM_RAW_REPLAY_CANDIDATE_V0_3.md` | `4f1d6a6e44356e21...` | 3343 |
| `docs/project/G13_THREE_ARM_RAW_REPLAY_CANDIDATE_V0_4.md` | `c0e480eb5f7dfff5...` | 3592 |
| `docs/project/G4_PROTOCOL_V34_APPROVAL_CHANNEL.md` | `70c0bb07709924fc...` | 9031 |
| `docs/project/G4_PROTOCOL_V35_CAUSAL_EVIDENCE.md` | `feeb5552c0d125f0...` | 3748 |
| `docs/project/G4_PROTOCOL_V36_SETTLING_DEADLINE.md` | `09e4096571736737...` | 3220 |
| `docs/project/G7_D2_PINNED_MODEL_V1.md` | `0f3657378add0b79...` | 2632 |
| `docs/project/G7_D3_CANDIDATE_REVIEW_V1.md` | `8cd83bb9bda5e609...` | 6589 |
| `docs/project/G7_D3_INDEPENDENT_PREFREEZE_REVIEW.md` | `1ef5eca3ae5203a4...` | 3576 |
| `docs/project/G7_D3_PREFREEZE_CHECKPOINT.md` | `6f5bd7fb051db624...` | 6768 |
| `docs/project/G7_D3_PREPARATION_APPROVAL_V1.md` | `22be898fc999b935...` | 2881 |
| `docs/project/G7_G13_DECISION_REGISTER.md` | `bef94fe30306bf51...` | 8112 |
| `docs/project/G7_G13_REMOTE_EXECUTION_RUNBOOK.md` | `35b50af21b3ba031...` | 19099 |
| `docs/project/G7_G9_REMOTE_TRAINING_READINESS.md` | `b4b6a7017311087c...` | 18716 |
| `docs/project/G7_NAMED_HOST_EXECUTION_PLAN_V1.md` | `622799d84481a28d...` | 10442 |
| `docs/project/G7_PILOT_ENVIRONMENT_V1.md` | `2ad27e228665096a...` | 8961 |
| `docs/project/G7_PILOT_EXECUTION_RECORD_V1.md` | `a6350f5c7925e917...` | 4918 |
| `docs/project/G7_PILOT_PREPARATION_REVIEW_V1.md` | `bf83f86ca141ce8b...` | 5680 |
| `docs/project/G7_REMOTE_HOST_OPTIONS_V1.md` | `7fd02277957640a0...` | 15892 |
| `docs/project/G7_SETTLING_SOURCE_COMPATIBILITY_V1.md` | `b9cd624b42a61ebe...` | 1990 |
| `docs/project/G7_SFT_PILOT_ACCEPTANCE_V1.md` | `ad80ca7f9d313bf0...` | 13171 |
| `docs/project/G8_D12_PRE_RL_RESOLUTION_PROPOSAL_V1.md` | `486b2d4a2ca1c41a...` | 3629 |
| `docs/project/G9_ASYNC_OBSERVATION_BRIDGE_V1.md` | `c1c3b38420bf98c3...` | 4572 |
| `docs/project/G9_OBSERVATION_FIRST_PROTOCOL_V1_PROPOSAL.md` | `daf95dfa2d90f24b...` | 4705 |
| `docs/project/G9_OBSERVATION_FIRST_SOFTWARE_CANDIDATE_V1.md` | `28822e2de8746bea...` | 5236 |
| `docs/project/G9_PROTOCOL_STANDALONE_P1_APPROVAL.md` | `856315008207a5e4...` | 4483 |
| `docs/project/GAI_RL_SCOPE_REVISION.md` | `24b83eaf30f831cc...` | 4993 |
| `docs/project/IMPLEMENTATION_STATUS.md` | `17abdc2b0a036015...` | 4149 |
| `docs/project/MASTER_PIPELINE_STATUS.md` | `b5801a3a3045bdf3...` | 28733 |
| `docs/project/STAGE_10_RECOMMENDER_DATA_AND_BASELINES.md` | `d0989c81494b0b1a...` | 2043 |
| `docs/project/STAGE_11_HYBRID_RECOMMENDER.md` | `be17afca2427c7e1...` | 2043 |
| `docs/project/STAGE_12_INTEGRATED_PIPELINE.md` | `629524d0644cfc50...` | 8740 |
| `docs/project/STAGE_13_FINAL_ABLATION_EVALUATION.md` | `676e015866eca023...` | 10960 |
| `docs/project/STAGE_14_DEPLOY_FINAL_DEMO.md` | `5eb9a8df01f8ca6f...` | 3057 |
| `docs/project/STAGE_15_FINAL_SUBMISSION.md` | `b1bf24f7d88a636f...` | 3118 |
| `docs/project/STAGE_3_OPERATOR_GUIDE.md` | `4903f3daff5982f4...` | 11698 |
| `docs/project/STAGE_5_SCENARIO_TRUTH_AND_SPLITS.md` | `9deaabcca82444f9...` | 8997 |
| `docs/project/STAGE_6_ZERO_SHOT_BASELINE.md` | `5ccb24f4ad94c9e0...` | 7872 |
| `docs/project/STAGE_7_SFT_DATA_AND_TRAINING.md` | `6c23ac98f75b7ea6...` | 11144 |
| `docs/project/STAGE_8_SFT_EVALUATION.md` | `677c001e637757d8...` | 7954 |
| `docs/project/STAGE_9_ONLINE_GRPO.md` | `846b7ebcbc3b4762...` | 12667 |
| `docs/project/UPSTREAM_ALIGNMENT_AUDIT_REPORT.md` | `f62340b22b77dbbe...` | 10277 |
| `docs/project/UPSTREAM_README_CURRENT_GAP_MATRIX.md` | `14b0ae256c863123...` | 20215 |
| `docs/slides.md` | `c5bf2053fd4867ff...` | 5995 |
| `eval.py` | `49d8b006daf1939b...` | 8692 |
| `infra/training/sft-pilot/Dockerfile` | `96cfc6591dddfd22...` | 611 |
| `infra/training/sft-pilot/Dockerfile.host-v1` | `d6782a753908a3b0...` | 1456 |
| `infra/training/sft-pilot/start-host.sh` | `461620370d8b4610...` | 600 |
| `leaderboard.py` | `110b1dd3bffe80f1...` | 15804 |
| `pyproject.toml` | `97d22256c6f75f8b...` | 1383 |
| `recommender/baselines.py` | `06e8e8e62a82695c...` | 7352 |
| `recommender/dataset.py` | `aa1a6c72e0d911fa...` | 13236 |
| `recommender/hybrid.py` | `41cba4b3e6e0c5f6...` | 9135 |
| `recommender/train_hybrid.py` | `337f19161334ed57...` | 6053 |
| `requirements/sft-pilot-linux-py312.lock` | `b649bfa91f1232b9...` | 167639 |
| `requirements/sft-pilot-v1.in` | `605781e837ab301a...` | 717 |
| `scripts/collect_sft_remote_provenance.py` | `cb2c1d042a310328...` | 30612 |
| `scripts/freeze_sft_pilot_plan.py` | `9239463af35384a4...` | 3076 |
| `scripts/package_submission.py` | `18ab9b3be477a425...` | 16730 |
| `scripts/release_gate.py` | `39f87773681a2f9a...` | 18205 |
| `scripts/run_g12_integrated_episode.py` | `eef1341de22994e2...` | 45882 |
| `scripts/run_stage4_golden_incident.py` | `24b4e51c0173fbe0...` | 101586 |
| `scripts/stage_sft_tokenizer.py` | `e9f70172eee6db96...` | 2883 |
| `static/console.css` | `46419d9f06618388...` | 23843 |
| `static/console.js` | `27f4c68073251679...` | 62151 |
| `static/index.html` | `2a7b6827b36a630b...` | 4771 |
| `static/live-incident.js` | `141c4482bd54d61c...` | 4684 |
| `static/live-incident.test.js` | `cc9e10c524e19752...` | 3551 |
| `static/vendor/LUCIDE-LICENSE` | `1e7290b35280a048...` | 880 |
| `static/vendor/lucide.min.js` | `3411692820cb8d47...` | 357796 |
| `tests/fixtures/TRL_LICENSE.txt` | `1d90ecbf7bb80ff2...` | 11355 |
| `tests/fixtures/trl_0191_observation_routing.txt` | `94f14b68b6f5e70b...` | 5562 |
| `tests/g9_approval_process.py` | `cb2f118d4a33e7a1...` | 2307 |
| `tests/stage4_approval_process.py` | `49df4be30e8edda0...` | 2416 |
| `tests/test_adversarial_designer.py` | `79f017da4584c382...` | 18677 |
| `tests/test_agents_grounding.py` | `95f1ecee76c33d6f...` | 17184 |
| `tests/test_app_endpoints.py` | `03cfab59acae6057...` | 6964 |
| `tests/test_approval.py` | `920d02620bbd67a2...` | 8347 |
| `tests/test_approval_fail_closed.py` | `7c8a92f09acce480...` | 8456 |
| `tests/test_argocd.py` | `87874c569f40a989...` | 11814 |
| `tests/test_argocd_error_taxonomy.py` | `c6b43f9a2b9ac2b7...` | 6802 |
| `tests/test_audit.py` | `7a2a10bda3a28e61...` | 3176 |
| `tests/test_bench_runner.py` | `90bcee2d80aa5bff...` | 19865 |
| `tests/test_bootstrap_lifecycle.py` | `68956a108f630f7c...` | 29227 |
| `tests/test_candidate_adapters.py` | `fd2773fe820516e9...` | 32497 |
| `tests/test_candidate_lineage.py` | `c9082ca28b7ae2a3...` | 16072 |
| `tests/test_candidate_measurement.py` | `f0549c53b5359791...` | 34087 |
| `tests/test_candidate_replay.py` | `03fa6cc230bba4f9...` | 48700 |
| `tests/test_chaos_manifests.py` | `28f20b4ad5be7030...` | 5832 |
| `tests/test_chaos_tools.py` | `5224b2bdc06bb15f...` | 9033 |
| `tests/test_circuit_breaker.py` | `eb4f927ce31ffe65...` | 4714 |
| `tests/test_claim_integrity.py` | `963b522837714c2b...` | 5831 |
| `tests/test_coordinator.py` | `84186590ad8a7e8d...` | 18704 |
| `tests/test_coordinator_import_side_effects.py` | `9b07ef640ae58481...` | 1095 |
| `tests/test_coordinator_turn_observability.py` | `edc1f4b08135e9f3...` | 13518 |
| `tests/test_correlator.py` | `ce0f51ecaf9ff9ee...` | 2212 |
| `tests/test_diagnosis_prompt_contract.py` | `8e1dc152aad848ce...` | 1001 |
| `tests/test_frontend_ui.py` | `65ccf5bf652001cd...` | 6058 |
| `tests/test_g12_policy_integration_contract.py` | `0a1861426113d85a...` | 19328 |
| `tests/test_g12_real_capture.py` | `101a9b761be01df2...` | 61638 |
| `tests/test_g13_lossless_g9_observations.py` | `e33a2fedbd3dd484...` | 12140 |
| `tests/test_g4_protocol_profile.py` | `164527c1cd603118...` | 22502 |
| `tests/test_g4_runtime_context_remediation_loop.py` | `3e23f0441e3645d1...` | 22261 |
| `tests/test_g4_v31_transport_and_interruption.py` | `a2e857f98851c770...` | 34823 |
| `tests/test_g6_empirical_contract.py` | `f0ebefb5c0b67de9...` | 67630 |
| `tests/test_g8_empirical_contract.py` | `eeee7e5a78aefdbc...` | 60295 |
| `tests/test_g9_approval_channel.py` | `39471f4e566ce28c...` | 22302 |
| `tests/test_g9_direct_policy_environment.py` | `46452232a0190875...` | 87207 |
| `tests/test_g9_direct_reward.py` | `1e2c5562f6b12aa9...` | 11861 |
| `tests/test_g9_empirical_contract.py` | `4036f8c5f83992aa...` | 41626 |
| `tests/test_g9_training_preflight.py` | `2d135056c3a8fd6d...` | 26182 |
| `tests/test_grpo_async_approval_integration.py` | `18224eaba5b5c8d1...` | 11534 |
| `tests/test_grpo_async_lifecycle.py` | `eaf969e9a791954f...` | 13073 |
| `tests/test_grpo_observation_first.py` | `4b1e6a78c8baf8af...` | 30519 |
| `tests/test_grpo_observation_trl_routing.py` | `2e55a142969a089d...` | 5588 |
| `tests/test_grpo_training_provenance.py` | `f6b35b6ef47822e8...` | 78952 |
| `tests/test_hf_space_env.py` | `74a310256bb35820...` | 3293 |
| `tests/test_http_retry.py` | `d02cafae0d281658...` | 12800 |
| `tests/test_infra_contract.py` | `acf912e9f656d248...` | 14569 |
| `tests/test_judge_outage_provenance.py` | `f9327e7055a73328...` | 6426 |
| `tests/test_judge_tier.py` | `f3785d7864e22b96...` | 635 |
| `tests/test_kubectl_top_classification.py` | `f9300671869cef0f...` | 2383 |
| `tests/test_local_infra_contract.py` | `c24eee168a5d1c1a...` | 11604 |
| `tests/test_local_metrics_installer.py` | `624f272690b2ee4d...` | 2664 |
| `tests/test_release_gate.py` | `911bb0b14d07deec...` | 13992 |
| `tests/test_reward_tool_policy.py` | `504584e6937d2ce2...` | 1387 |
| `tests/test_rs_dataset_provenance.py` | `cf14c29cb8b64929...` | 9462 |
| `tests/test_rs_runtime_query_isolation.py` | `6697c24640643f9e...` | 1945 |
| `tests/test_rs_stage11_provenance.py` | `3105c5b00b268859...` | 2073 |
| `tests/test_runtime_infra_contract.py` | `7c8bb85576f5a0cc...` | 13616 |
| `tests/test_sft_candidate_admission.py` | `8f4c9887caa53eea...` | 8815 |
| `tests/test_sft_candidate_builder.py` | `7dc8edf653103fbf...` | 15391 |
| `tests/test_sft_data_contract.py` | `682ba063b4822bb8...` | 8638 |
| `tests/test_sft_host_plan.py` | `6fbf7ecf4f96815e...` | 3782 |
| `tests/test_sft_mask_proof.py` | `4103befd97c834c9...` | 8419 |
| `tests/test_sft_pilot_gate.py` | `fe824986e30af452...` | 15596 |
| `tests/test_sft_qwen_template_render.py` | `9a0129cf11a9cb9e...` | 6443 |
| `tests/test_sft_remote_provenance.py` | `c84e76087e6fc17e...` | 12781 |
| `tests/test_sft_template_wiring.py` | `a0e173cfd46960b9...` | 10667 |
| `tests/test_sft_tokenizer_pilot_extension.py` | `4b27b7a3fde1b819...` | 12571 |
| `tests/test_sft_tokenizer_preflight.py` | `e7b4132444b006bb...` | 13778 |
| `tests/test_sft_trainer_handoff.py` | `be8afb602233c26e...` | 12934 |
| `tests/test_sft_training_provenance.py` | `897e4b0d1004aa60...` | 51143 |
| `tests/test_stage10_rs_data_and_baselines.py` | `a9c08a6fc88350c9...` | 6356 |
| `tests/test_stage11_hybrid_recommender.py` | `d0a7a55561f7e346...` | 7650 |
| `tests/test_stage12_integrated_pipeline.py` | `16ae8b1f12602c66...` | 6706 |
| `tests/test_stage13_ablation_suite.py` | `d93d6fd48d0560ed...` | 43463 |
| `tests/test_stage14_demo_safety.py` | `de91c0aa698264b7...` | 4345 |
| `tests/test_stage15_submission_package.py` | `1ffaa82ac6a49a29...` | 29718 |
| `tests/test_stage4_approval_channel.py` | `ec937815223eaf84...` | 13839 |
| `tests/test_stage4_baseline_prereservation.py` | `d8697de76a455da8...` | 5445 |
| `tests/test_stage4_causal_contract.py` | `95d09d77ea827772...` | 40407 |
| `tests/test_stage4_evidence_hardening.py` | `43b808a6bb12430b...` | 53148 |
| `tests/test_stage4_f1_envelope_contract.py` | `b1b199f4e043bc5a...` | 5200 |
| `tests/test_stage4_grounding_evidence.py` | `c6feaab32aabda8c...` | 1472 |
| `tests/test_stage4_model_selection.py` | `dea87ab61b7be494...` | 6249 |
| `tests/test_stage4_preflight_contract.py` | `8582a9082e024d79...` | 33085 |
| `tests/test_stage4_telemetry_readiness.py` | `2b3743601dc793a1...` | 16281 |
| `tests/test_stage5_scenario_splits_and_truth.py` | `af7025be02ca7b27...` | 5364 |
| `tests/test_stage6_zero_shot_baseline.py` | `07ef195e96e170cc...` | 6473 |
| `tests/test_stage7_sft_pipeline.py` | `f5589d2ca93f37d6...` | 17406 |
| `tests/test_stage8_sft_eval.py` | `e52c0051d088d45f...` | 6671 |
| `tests/test_stage9_grpo_pipeline.py` | `de3dce53390d9339...` | 14180 |
| `tests/test_tool_policy_contract.py` | `15f377b269d769da...` | 5022 |
| `tests/test_tools.py` | `ae53aff75ba44b80...` | 15558 |
| `tests/test_ui_read_model.py` | `1942099a2975dd69...` | 3093 |
| `tests/test_verifier.py` | `42820832c21e6f93...` | 27740 |
| `training/build_sft_candidate.py` | `c0dd9af9e94468d2...` | 81824 |
| `training/build_sft_dataset.py` | `237323d639d21a7b...` | 14343 |
| `training/generate_trajectories.py` | `490b20a96c1501a8...` | 5498 |
| `training/generate_trajectories_fast.py` | `773885747caed336...` | 411 |
| `training/grpo.py` | `d7d8e4bd20830567...` | 60300 |
| `training/grpo_async_lifecycle.py` | `4534ea10f44333d7...` | 10302 |
| `training/grpo_environment.py` | `8826379511dacda3...` | 31919 |
| `training/grpo_observation_first.py` | `77ed408e325f8162...` | 25618 |
| `training/grpo_provenance.py` | `8903bf0e576a9585...` | 50476 |
| `training/grpo_reward.py` | `23e7cde82209595e...` | 3180 |
| `training/sft.py` | `48596108d1ed9903...` | 15336 |
| `training/sft_candidate.py` | `9dbc4e7e4fbe1f8d...` | 27332 |
| `training/sft_candidate_compatibility.py` | `86762ad56c82f6eb...` | 2171 |
| `training/sft_pilot_gate.py` | `7d7a5ffa0e33fe40...` | 18469 |
| `training/sft_provenance.py` | `512a9a738607a691...` | 33709 |
| `training/sft_rendering.py` | `ed4e9a4c06e96029...` | 12524 |
| `training/sft_tokenizer_preflight.py` | `e0027cc2c0c6ff13...` | 44949 |
| `training/templates/qwen2_5_tool_sft.jinja` | `72c06246b2ce2bbd...` | 3346 |
| `ui_read_model.py` | `859db572c2706d7d...` | 10012 |
