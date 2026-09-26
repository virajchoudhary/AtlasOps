# AtlasOps — Submission Readiness and Asset Inventory

- **Project Repository**: `virajchoudhary/AtlasOps`
- **Upstream Baseline**: `Harikishanth/AtlasOps @ bf9bd19`
- **Certification**: **NOT_CERTIFIED**
- **Generated**: `2026-09-26T16:39:11.545856+00:00`
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
| `.gitattributes` | `b7ecde5620295b79...` | 798 |
| `BENCHMARKS.md` | `469f977169a3cd56...` | 4549 |
| `Makefile` | `34bbfe03619f49b1...` | 4129 |
| `README.md` | `793d45179291d309...` | 21520 |
| `agents/approval.py` | `906647ebb08d1858...` | 3851 |
| `agents/coordinator.py` | `5f3b7dbe8269421c...` | 110412 |
| `agents/grounding.py` | `bd1141dd29d97a24...` | 6759 |
| `agents/judge.py` | `b817d1433d43e70a...` | 7853 |
| `agents/policy_remediation.py` | `d071d7f9046c38d8...` | 4511 |
| `agents/prompts/comms.md` | `be58db4dcef9b422...` | 2662 |
| `agents/prompts/diagnosis.md` | `26265a2007477eed...` | 2902 |
| `agents/prompts/remediation.md` | `3945dfd98dfda25d...` | 4513 |
| `agents/prompts/triage.md` | `956a1489758cb0fe...` | 2181 |
| `agents/tool_policy.py` | `c7c755b1fa33c9b7...` | 2884 |
| `agents/tools/argocd.py` | `5cfdad11258fcf68...` | 10784 |
| `agents/tools/chaos.py` | `bdba977da1a3c657...` | 5801 |
| `agents/tools/prometheus.py` | `dc8d87332f66dae8...` | 3586 |
| `app.py` | `d17466d5be9a5300...` | 17228 |
| `artifacts/evidence/stage10/rs_dataset_manifest.json` | `69d7bfa78cccda00...` | 587 |
| `artifacts/evidence/stage11/rs_hybrid_eval.json` | `2ff967237a7bf21c...` | 3234 |
| `artifacts/evidence/stage11/rs_hybrid_eval_synthetic_v2.json` | `fe32b345c8bcddf3...` | 4544 |
| `artifacts/evidence/stage13/ablation_benchmark_results.json` | `3b7f1f88e6e37a96...` | 7156 |
| `artifacts/models/hybrid_recommender.json` | `53d0a4fb640b4691...` | 2646 |
| `artifacts/models/hybrid_recommender_synthetic_v2.json` | `21243d035fc2a972...` | 2192 |
| `artifacts/overnight_experiments/rs-20260924/interactions.jsonl` | `4e60a94564124bb9...` | 8750 |
| `artifacts/overnight_experiments/rs-20260924/interactions.manifest.json` | `57dfd45e856bf8f2...` | 9329 |
| `bench/ablation_suite.py` | `957c4bc2ee0a3d1f...` | 12406 |
| `bench/grpo_eval.py` | `8c6aa528de0289b3...` | 50036 |
| `bench/runner.py` | `6e75db82c21d1ad5...` | 15551 |
| `bench/sft_eval.py` | `4e13d1aa99c62031...` | 23096 |
| `bench/zero_shot_baseline.py` | `c0fd30a86fd3b994...` | 19879 |
| `config/g4_protocol.py` | `067d7e1bb04a75f6...` | 20851 |
| `config/runtime.py` | `96fe1d1834846cb3...` | 18567 |
| `dashboard.py` | `7e4afc02551bf4d6...` | 23653 |
| `demo/launcher.py` | `a9d51bc21b7e45dc...` | 1744 |
| `docs/AtlasOps_Technical_Report.md` | `50a354b5e59e8e6b...` | 7749 |
| `docs/BENCHMARKS.md` | `f4054130730f4981...` | 2831 |
| `docs/END_TO_END_FLOW.md` | `3e594773cf15c6b7...` | 5629 |
| `docs/HF_SPACE_SETUP.md` | `b9fd2597fb62d620...` | 8350 |
| `docs/media/console-overview-20260926.png` | `4e9d204e953ee2f9...` | 101431 |
| `docs/media/gradio-demo-20260926.png` | `399c06bad7c2dca0...` | 53821 |
| `docs/project/G4_PROTOCOL_V34_APPROVAL_CHANNEL.md` | `f61575d3b3f0a586...` | 4419 |
| `docs/project/MASTER_PIPELINE_STATUS.md` | `7450a39b4240683d...` | 25551 |
| `docs/project/STAGE_10_RECOMMENDER_DATA_AND_BASELINES.md` | `3a31a019bb012392...` | 1774 |
| `docs/project/STAGE_11_HYBRID_RECOMMENDER.md` | `788de77730246d91...` | 1751 |
| `docs/project/STAGE_12_INTEGRATED_PIPELINE.md` | `6d6631ce9c3c8b9a...` | 3457 |
| `docs/project/STAGE_13_FINAL_ABLATION_EVALUATION.md` | `cb1c07a038e191ae...` | 2220 |
| `docs/project/STAGE_14_DEPLOY_FINAL_DEMO.md` | `5eb9a8df01f8ca6f...` | 3057 |
| `docs/project/STAGE_15_FINAL_SUBMISSION.md` | `c0b8b5e374aa911f...` | 2563 |
| `docs/project/STAGE_3_OPERATOR_GUIDE.md` | `4903f3daff5982f4...` | 11698 |
| `docs/project/STAGE_5_SCENARIO_TRUTH_AND_SPLITS.md` | `9deaabcca82444f9...` | 8997 |
| `docs/project/STAGE_6_ZERO_SHOT_BASELINE.md` | `d450ce1d61e1fef1...` | 2093 |
| `docs/project/STAGE_7_SFT_DATA_AND_TRAINING.md` | `6e9eec0d90f873d1...` | 3715 |
| `docs/project/STAGE_8_SFT_EVALUATION.md` | `79f294072981a62f...` | 1690 |
| `docs/project/STAGE_9_ONLINE_GRPO.md` | `45e5249fd5284dd8...` | 3595 |
| `docs/slides.md` | `04aa2ff18b4f2e4c...` | 5890 |
| `eval.py` | `49d8b006daf1939b...` | 8692 |
| `leaderboard.py` | `110b1dd3bffe80f1...` | 15804 |
| `recommender/baselines.py` | `06e8e8e62a82695c...` | 7352 |
| `recommender/dataset.py` | `aa1a6c72e0d911fa...` | 13236 |
| `recommender/hybrid.py` | `41cba4b3e6e0c5f6...` | 9135 |
| `recommender/train_hybrid.py` | `337f19161334ed57...` | 6053 |
| `scripts/package_submission.py` | `8491ab6c2b72637b...` | 8543 |
| `scripts/run_g12_integrated_episode.py` | `534781631814e86a...` | 11177 |
| `scripts/run_stage4_golden_incident.py` | `15194c94c0820560...` | 85332 |
| `static/console.css` | `46419d9f06618388...` | 23843 |
| `static/console.js` | `277ba766de0a84b2...` | 64395 |
| `static/index.html` | `d2d907afdfd046c5...` | 4760 |
| `static/live-incident.js` | `141c4482bd54d61c...` | 4684 |
| `static/live-incident.test.js` | `cc9e10c524e19752...` | 3551 |
| `static/vendor/LUCIDE-LICENSE` | `1e7290b35280a048...` | 880 |
| `static/vendor/lucide.min.js` | `3411692820cb8d47...` | 357796 |
| `tests/stage4_approval_process.py` | `49df4be30e8edda0...` | 2416 |
| `tests/test_adversarial_designer.py` | `e7e9d4f038571eeb...` | 5365 |
| `tests/test_agents_grounding.py` | `9d7b64c0b0249273...` | 8244 |
| `tests/test_app_endpoints.py` | `03cfab59acae6057...` | 6964 |
| `tests/test_approval.py` | `62c82fa66d0c8ec1...` | 1392 |
| `tests/test_approval_fail_closed.py` | `b8cce161aee66a1e...` | 7789 |
| `tests/test_argocd.py` | `87874c569f40a989...` | 11814 |
| `tests/test_argocd_error_taxonomy.py` | `c6b43f9a2b9ac2b7...` | 6802 |
| `tests/test_audit.py` | `7a2a10bda3a28e61...` | 3176 |
| `tests/test_bench_runner.py` | `90bcee2d80aa5bff...` | 19865 |
| `tests/test_bootstrap_lifecycle.py` | `68956a108f630f7c...` | 29227 |
| `tests/test_chaos_manifests.py` | `28f20b4ad5be7030...` | 5832 |
| `tests/test_circuit_breaker.py` | `eb4f927ce31ffe65...` | 4714 |
| `tests/test_coordinator.py` | `e82e21b5326ca7f3...` | 18298 |
| `tests/test_coordinator_turn_observability.py` | `edc1f4b08135e9f3...` | 13518 |
| `tests/test_correlator.py` | `ce0f51ecaf9ff9ee...` | 2212 |
| `tests/test_diagnosis_prompt_contract.py` | `8e1dc152aad848ce...` | 1001 |
| `tests/test_frontend_ui.py` | `172e063d5e638158...` | 5251 |
| `tests/test_g12_policy_integration_contract.py` | `708f4a6a526dcaab...` | 8113 |
| `tests/test_g12_real_capture.py` | `5ff4bd9cb60d99ad...` | 9506 |
| `tests/test_g4_protocol_profile.py` | `2ee5fcc5a228a883...` | 19121 |
| `tests/test_g4_runtime_context_remediation_loop.py` | `79debd0c2024bea2...` | 22071 |
| `tests/test_g4_v31_transport_and_interruption.py` | `7ddb615759077b9d...` | 24092 |
| `tests/test_g6_empirical_contract.py` | `8b21fef1a4c367c6...` | 5846 |
| `tests/test_g8_empirical_contract.py` | `647c06dd2576eca8...` | 9019 |
| `tests/test_g9_direct_policy_environment.py` | `566f8bd0ef2369a8...` | 14668 |
| `tests/test_g9_empirical_contract.py` | `e1c992af32a1e52e...` | 16217 |
| `tests/test_grpo_training_provenance.py` | `e1fa1f80465ce5b5...` | 8849 |
| `tests/test_hf_space_env.py` | `74a310256bb35820...` | 3293 |
| `tests/test_http_retry.py` | `ab782b8c5d1c70ae...` | 2569 |
| `tests/test_infra_contract.py` | `e4ee2ab5e3927cb1...` | 14459 |
| `tests/test_judge_outage_provenance.py` | `f9327e7055a73328...` | 6426 |
| `tests/test_judge_tier.py` | `f3785d7864e22b96...` | 635 |
| `tests/test_kubectl_top_classification.py` | `f9300671869cef0f...` | 2383 |
| `tests/test_local_infra_contract.py` | `c24eee168a5d1c1a...` | 11604 |
| `tests/test_local_metrics_installer.py` | `624f272690b2ee4d...` | 2664 |
| `tests/test_reward_tool_policy.py` | `504584e6937d2ce2...` | 1387 |
| `tests/test_rs_dataset_provenance.py` | `cf14c29cb8b64929...` | 9462 |
| `tests/test_rs_runtime_query_isolation.py` | `6697c24640643f9e...` | 1945 |
| `tests/test_rs_stage11_provenance.py` | `3105c5b00b268859...` | 2073 |
| `tests/test_runtime_infra_contract.py` | `7c8bb85576f5a0cc...` | 13616 |
| `tests/test_sft_data_contract.py` | `682ba063b4822bb8...` | 8638 |
| `tests/test_sft_mask_proof.py` | `4103befd97c834c9...` | 8419 |
| `tests/test_sft_qwen_template_render.py` | `9a0129cf11a9cb9e...` | 6443 |
| `tests/test_sft_template_wiring.py` | `fcc4bd53b53598df...` | 10382 |
| `tests/test_sft_training_provenance.py` | `8205531eeb4bff78...` | 7365 |
| `tests/test_stage10_rs_data_and_baselines.py` | `a9c08a6fc88350c9...` | 6356 |
| `tests/test_stage11_hybrid_recommender.py` | `d0a7a55561f7e346...` | 7650 |
| `tests/test_stage12_integrated_pipeline.py` | `d3acd12c5d770d60...` | 5945 |
| `tests/test_stage13_ablation_suite.py` | `1e9c229c401d18a6...` | 8746 |
| `tests/test_stage14_demo_safety.py` | `de91c0aa698264b7...` | 4345 |
| `tests/test_stage15_submission_package.py` | `7957671759c2002b...` | 4683 |
| `tests/test_stage4_approval_channel.py` | `73d03fc83793fcfb...` | 12720 |
| `tests/test_stage4_baseline_prereservation.py` | `b8e71a61a1bd6ce1...` | 5447 |
| `tests/test_stage4_causal_contract.py` | `fe8354613ff2720b...` | 38102 |
| `tests/test_stage4_evidence_hardening.py` | `1e26c9043960fc16...` | 44607 |
| `tests/test_stage4_f1_envelope_contract.py` | `12d60e468e760a64...` | 5200 |
| `tests/test_stage4_grounding_evidence.py` | `c6feaab32aabda8c...` | 1472 |
| `tests/test_stage4_model_selection.py` | `985a3f66808cccb2...` | 6272 |
| `tests/test_stage4_telemetry_readiness.py` | `05e4114535263220...` | 15676 |
| `tests/test_stage5_scenario_splits_and_truth.py` | `af7025be02ca7b27...` | 5364 |
| `tests/test_stage6_zero_shot_baseline.py` | `483ea70196282110...` | 6032 |
| `tests/test_stage7_sft_pipeline.py` | `5265a4a1bc235d4b...` | 8151 |
| `tests/test_stage8_sft_eval.py` | `24c8786bda4aba1c...` | 4007 |
| `tests/test_stage9_grpo_pipeline.py` | `5a8ccf28e799ce6d...` | 4777 |
| `tests/test_tool_policy_contract.py` | `15f377b269d769da...` | 5022 |
| `tests/test_tools.py` | `ae53aff75ba44b80...` | 15558 |
| `tests/test_ui_read_model.py` | `1942099a2975dd69...` | 3093 |
| `tests/test_verifier.py` | `42820832c21e6f93...` | 27740 |
| `training/build_sft_dataset.py` | `b3399a94654cacb2...` | 10765 |
| `training/generate_trajectories.py` | `490b20a96c1501a8...` | 5498 |
| `training/generate_trajectories_fast.py` | `773885747caed336...` | 411 |
| `training/grpo.py` | `1040ef5f92e3bca9...` | 27747 |
| `training/grpo_environment.py` | `51dec0f0383268a3...` | 10583 |
| `training/grpo_provenance.py` | `c63871f5d484201e...` | 6851 |
| `training/sft.py` | `29ff6fa2ae27ec89...` | 9091 |
| `training/sft_provenance.py` | `30981f180779bd04...` | 10157 |
| `ui_read_model.py` | `859db572c2706d7d...` | 10012 |
