# AtlasOps — Submission Readiness and Asset Inventory

- **Project Repository**: `virajchoudhary/AtlasOps`
- **Upstream Baseline**: `Harikishanth/AtlasOps @ bf9bd19`
- **Certification**: **NOT_CERTIFIED**
- **Generated**: `2026-09-24T05:03:30.894554+00:00`
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
| G10 | PARTIAL |
| G11 | PASS |
| G12 | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING |
| G13 | REOPENED |
| G14 | PARTIAL |
| G15 | PARTIAL |

No full-pipeline empirical metric is certified by this asset inventory.
Asset hashes establish file integrity, not scientific gate closure.

## Canonical Submission Artifacts

| Asset Path | SHA-256 Digest | Size (Bytes) |
| :--- | :--- | :---: |
| `agents/coordinator.py` | `9a39ccedddc45bc4...` | 112988 |
| `agents/grounding.py` | `138947ddd5721717...` | 6946 |
| `agents/policy_remediation.py` | `d071d7f9046c38d8...` | 4511 |
| `agents/prompts/comms.md` | `2c57ba84a205ff16...` | 2713 |
| `agents/prompts/diagnosis.md` | `1ca2395c33f38e83...` | 2962 |
| `agents/prompts/remediation.md` | `9323c72cb2775064...` | 4590 |
| `agents/prompts/triage.md` | `6b9faf13dd390365...` | 2231 |
| `agents/tool_policy.py` | `2c4ca8950b71055b...` | 2971 |
| `agents/tools/argocd.py` | `791711502838ebcb...` | 11082 |
| `agents/tools/chaos.py` | `26bcdbdfe76132bd...` | 5987 |
| `agents/tools/prometheus.py` | `472d7bc8c3027f8f...` | 3691 |
| `artifacts/evidence/stage10/rs_dataset_manifest.json` | `69d7bfa78cccda00...` | 587 |
| `artifacts/evidence/stage11/rs_hybrid_eval.json` | `2ff967237a7bf21c...` | 3234 |
| `artifacts/evidence/stage11/rs_hybrid_eval_synthetic_v2.json` | `fe32b345c8bcddf3...` | 4544 |
| `artifacts/evidence/stage13/ablation_benchmark_results.json` | `3b7f1f88e6e37a96...` | 7156 |
| `artifacts/models/hybrid_recommender.json` | `53d0a4fb640b4691...` | 2646 |
| `artifacts/models/hybrid_recommender_synthetic_v2.json` | `21243d035fc2a972...` | 2192 |
| `artifacts/overnight_experiments/rs-20260924/interactions.jsonl` | `4e60a94564124bb9...` | 8750 |
| `artifacts/overnight_experiments/rs-20260924/interactions.manifest.json` | `57dfd45e856bf8f2...` | 9329 |
| `bench/ablation_suite.py` | `cbfa7df59050900c...` | 12708 |
| `bench/grpo_eval.py` | `978c5d8b5f83646c...` | 51193 |
| `bench/sft_eval.py` | `8ba015902dbcd676...` | 23672 |
| `bench/zero_shot_baseline.py` | `849ac0a00064dd9e...` | 20407 |
| `config/g4_protocol.py` | `93047c382f5a5591...` | 20358 |
| `dashboard.py` | `5a310cbcb499fc11...` | 15991 |
| `demo/launcher.py` | `6bf806870d5cb200...` | 1852 |
| `docs/AtlasOps_Technical_Report.md` | `50a354b5e59e8e6b...` | 7749 |
| `docs/project/MASTER_PIPELINE_STATUS.md` | `932f1f4315a03915...` | 25336 |
| `docs/project/STAGE_10_RECOMMENDER_DATA_AND_BASELINES.md` | `3a31a019bb012392...` | 1774 |
| `docs/project/STAGE_11_HYBRID_RECOMMENDER.md` | `788de77730246d91...` | 1751 |
| `docs/project/STAGE_12_INTEGRATED_PIPELINE.md` | `86bce088c80b583f...` | 1905 |
| `docs/project/STAGE_13_FINAL_ABLATION_EVALUATION.md` | `cb1c07a038e191ae...` | 2220 |
| `docs/project/STAGE_14_DEPLOY_FINAL_DEMO.md` | `deea56be87100f94...` | 3586 |
| `docs/project/STAGE_15_FINAL_SUBMISSION.md` | `e6953480e28cdb16...` | 1830 |
| `docs/project/STAGE_3_OPERATOR_GUIDE.md` | `a21f1dd34f26ad73...` | 11922 |
| `docs/project/STAGE_5_SCENARIO_TRUTH_AND_SPLITS.md` | `cd9bca6b5062f47e...` | 9110 |
| `docs/project/STAGE_6_ZERO_SHOT_BASELINE.md` | `71829593c8457c85...` | 1828 |
| `docs/project/STAGE_7_SFT_DATA_AND_TRAINING.md` | `0b531df53e3fe032...` | 2031 |
| `docs/project/STAGE_8_SFT_EVALUATION.md` | `79f294072981a62f...` | 1690 |
| `docs/project/STAGE_9_ONLINE_GRPO.md` | `45e5249fd5284dd8...` | 3595 |
| `recommender/baselines.py` | `09ccdcbb7c0eca97...` | 7561 |
| `recommender/dataset.py` | `bd915e300b2658b2...` | 13564 |
| `recommender/hybrid.py` | `3b1def13e6df2dda...` | 9384 |
| `recommender/train_hybrid.py` | `f3ce3bfa1cd2000f...` | 6208 |
| `scripts/run_stage4_golden_incident.py` | `eea3c69a77eb7098...` | 83238 |
| `tests/test_adversarial_designer.py` | `5fb3c668ab65d48b...` | 5490 |
| `tests/test_agents_grounding.py` | `f2d9454c45630193...` | 8482 |
| `tests/test_app_endpoints.py` | `0cb81a8ca29b5f79...` | 4985 |
| `tests/test_approval.py` | `6f21b47df27f144e...` | 1434 |
| `tests/test_approval_fail_closed.py` | `41788fd689d7ad11...` | 7940 |
| `tests/test_argocd.py` | `ab7a14c799655665...` | 12125 |
| `tests/test_argocd_error_taxonomy.py` | `ddef1c5c05bc0a5e...` | 6986 |
| `tests/test_audit.py` | `c145a658a84e0ed1...` | 3264 |
| `tests/test_bench_runner.py` | `410c594568a6674a...` | 18481 |
| `tests/test_bootstrap_lifecycle.py` | `95c70ec585751a9d...` | 29846 |
| `tests/test_chaos_manifests.py` | `d754dbc7014c4a4c...` | 5996 |
| `tests/test_circuit_breaker.py` | `29627d0999f63e34...` | 4837 |
| `tests/test_coordinator.py` | `b2300738d26ce25e...` | 18715 |
| `tests/test_coordinator_turn_observability.py` | `7ca339544274756a...` | 13806 |
| `tests/test_correlator.py` | `a40685db247daf0e...` | 2271 |
| `tests/test_diagnosis_prompt_contract.py` | `1e32bb488f7efd15...` | 1031 |
| `tests/test_frontend_ui.py` | `16bf3328cae829ba...` | 3453 |
| `tests/test_g12_policy_integration_contract.py` | `708f4a6a526dcaab...` | 8113 |
| `tests/test_g4_protocol_profile.py` | `40f2b3bb105160d8...` | 18446 |
| `tests/test_g4_runtime_context_remediation_loop.py` | `823ea0b00845d1ba...` | 22702 |
| `tests/test_g4_v31_transport_and_interruption.py` | `0cc5276dbde0a391...` | 24493 |
| `tests/test_g6_empirical_contract.py` | `02cfd6a5540afe23...` | 6022 |
| `tests/test_g8_empirical_contract.py` | `d3b9713a1132863c...` | 9280 |
| `tests/test_g9_direct_policy_environment.py` | `3e0916c7a67b5a4a...` | 15079 |
| `tests/test_g9_empirical_contract.py` | `a807cf20323f9dc4...` | 16650 |
| `tests/test_grpo_training_provenance.py` | `e1fa1f80465ce5b5...` | 8849 |
| `tests/test_hf_space_env.py` | `2f5575e4a282c80b...` | 3388 |
| `tests/test_http_retry.py` | `92879c9849dcb1a9...` | 2662 |
| `tests/test_infra_contract.py` | `bdc5db742596fd5e...` | 14806 |
| `tests/test_judge_tier.py` | `8898a44f000ff5c2...` | 645 |
| `tests/test_kubectl_top_classification.py` | `fd2cdcbe9ab83a52...` | 2435 |
| `tests/test_local_infra_contract.py` | `355578348533b166...` | 11876 |
| `tests/test_local_metrics_installer.py` | `1823c2cfe11329b5...` | 2731 |
| `tests/test_rs_dataset_provenance.py` | `6dc1c838eed3fdee...` | 9698 |
| `tests/test_rs_runtime_query_isolation.py` | `6697c24640643f9e...` | 1945 |
| `tests/test_rs_stage11_provenance.py` | `3105c5b00b268859...` | 2073 |
| `tests/test_runtime_infra_contract.py` | `36766d0bcc525b4a...` | 13790 |
| `tests/test_sft_data_contract.py` | `9fa1f1b93a946f4f...` | 8346 |
| `tests/test_sft_mask_proof.py` | `d40f92598609ba47...` | 8629 |
| `tests/test_sft_qwen_template_render.py` | `5a8cb066329479f6...` | 6615 |
| `tests/test_sft_template_wiring.py` | `6697b4ba1690f297...` | 10638 |
| `tests/test_sft_training_provenance.py` | `4fd2253afccceb95...` | 7575 |
| `tests/test_stage10_rs_data_and_baselines.py` | `a7772b5fa0dc8217...` | 6525 |
| `tests/test_stage11_hybrid_recommender.py` | `13f328a9637d6aff...` | 7840 |
| `tests/test_stage12_integrated_pipeline.py` | `f165cc872fff7b28...` | 6081 |
| `tests/test_stage13_ablation_suite.py` | `f7e77bc6b9846331...` | 8975 |
| `tests/test_stage14_demo_safety.py` | `09eb5377f50ec4ec...` | 2846 |
| `tests/test_stage15_submission_package.py` | `4e624aa9e231e946...` | 3166 |
| `tests/test_stage4_baseline_prereservation.py` | `b3bf54769e30982a...` | 5598 |
| `tests/test_stage4_causal_contract.py` | `c4a0e6ddadca1274...` | 38976 |
| `tests/test_stage4_evidence_hardening.py` | `2af62d0a756a383e...` | 45782 |
| `tests/test_stage4_f1_envelope_contract.py` | `c83c756d90443805...` | 5335 |
| `tests/test_stage4_grounding_evidence.py` | `9199b0169859b841...` | 1520 |
| `tests/test_stage4_model_selection.py` | `cd68b9b34f045a74...` | 6438 |
| `tests/test_stage4_telemetry_readiness.py` | `0bcaef1c42fd13da...` | 16101 |
| `tests/test_stage5_scenario_splits_and_truth.py` | `396eab22f8a5229d...` | 5494 |
| `tests/test_stage6_zero_shot_baseline.py` | `af2e0b3f4f6f6836...` | 6162 |
| `tests/test_stage7_sft_pipeline.py` | `77becb14c75cc3de...` | 5790 |
| `tests/test_stage8_sft_eval.py` | `a835b3312fbb56c0...` | 4099 |
| `tests/test_stage9_grpo_pipeline.py` | `a393a31fd07f901e...` | 4892 |
| `tests/test_tool_policy_contract.py` | `5986f90792d31f32...` | 5167 |
| `tests/test_tools.py` | `a0b668f0965cea1d...` | 15894 |
| `tests/test_verifier.py` | `eab16242a38009ef...` | 28352 |
| `training/grpo.py` | `e05993214dcf6eba...` | 28467 |
| `training/grpo_environment.py` | `6e009de6b5246172...` | 10879 |
| `training/grpo_provenance.py` | `c63871f5d484201e...` | 6851 |
| `training/sft.py` | `2b82a5a41f2796fc...` | 9355 |
| `training/sft_provenance.py` | `60a29bbdd18e2a8d...` | 10462 |
