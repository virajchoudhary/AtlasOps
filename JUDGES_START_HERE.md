# AtlasOps: Reviewer Start Here

**Current continuation status: NOT_CERTIFIED.** AtlasOps is the university continuation of
Harikishanth/AtlasOps, preserving the upstream history, MIT license, and attribution. This
review packet records implementation evidence and the results that were actually observed.
It does not claim a successful live incident, a learned GRPO policy, or deployment
certification.

## What the Team Built

AtlasOps coordinates four specialized SRE roles: Triage, Diagnosis, Remediation, and
Comms. The implementation includes role-based tool access, an explicit P1 approval gate,
an objective environment verifier, frozen scenario-split governance, and evidence
provenance checks. The registry contains 24 SRE tool wrappers, with 19 exposed to
autonomous agents. Local tests exercise software contracts; they do not certify live
operations.

## Current Results

| Evidence | Observed result | What it does not establish |
|---|---|---|
| Base vs SFT Validation diagnostic | Base F1 0.16875; SFT F1 0.15935; paired delta -0.00940; schema 6/6 in each arm | The six-scenario alert-only diagnostic does not measure incident resolution, action validity, safety, reward, or TTR. No diagnostic improvement was observed. |
| SFT artifact v17 | Qwen2.5-7B-Instruct adapter, 68 synthetic Train-only rows, 9 optimizer steps, independent fresh-process reload passed | Artifact existence and reload do not show incident improvement. |
| Controlled G9 pilot | Two optimizer steps and four completions; all four were malformed or blocked, each reward was -1, and both advantage groups were zero | No reward-driven learning was established. No acceptable SFT+GRPO checkpoint exists. |
| Final aligned G9 diagnostic | 0/8 admissible actions, zero optimizer steps, and all 392 LoRA tensor hashes unchanged | Zero of eight does not prove that the population probability is exactly zero. |
| G4 live incident | Attempt 015 inconclusive and unscored; 016 pre-fault abort; 017 completed negative result | G4 remains NOT_PASSED. No attempt 018 exists. The original G8 live incident-resolution criterion remains unmet. |

The [Master Pipeline status](docs/project/MASTER_PIPELINE_STATUS.md) is the gate-status
source. The [technical report](docs/AtlasOps_Technical_Report.md) explains methods,
limitations, and provenance. The [presentation source](docs/slides.md) is organized for a
short project review.

## Five-Minute Review

1. Start the local read-only evidence demo from the repository root:

   ```powershell
   python -m demo.launcher --host 127.0.0.1 --port 7860
   ```

   Open `http://127.0.0.1:7860/`. The demo reads repository evidence and requires no
   Docker, Kind cluster, GPU, or model endpoint. It runs no model inference, kubectl
   command, fault injection, approval action, or remediation.

2. Show the current result summary above, then open
   [BASE_SFT_VALIDATION_RESULT_V1.md](docs/project/BASE_SFT_VALIDATION_RESULT_V1.md).
   Its linked run manifest, raw responses, scored episodes, and independent recomputations
   preserve the matched six-scenario Validation diagnostic.

3. Inspect [the v17 SFT run record](artifacts/evidence/stage7/free-t4-v17/RESULT.json),
   its [run manifest](artifacts/evidence/stage7/free-t4-v17/sft_run_manifest.json), and
   [independent reload record](artifacts/evidence/stage7/free-t4-v17/reload-v17.json).
   The preserved adapter is a bounded artifact trained from synthetic Train-only data.

4. Read [the final controlled G9 result](docs/project/CONTROLLED_G9_FINAL_NEGATIVE_V1.md)
   and the [aligned diagnostic](artifacts/evidence/stage9/final-aligned-diagnostic-v1/LOCAL_VERIFICATION.json).
   The concise diagnostic files are tracked. The full pilot archive is identified by
   SHA-256 in the result document and is not part of ordinary Git.

5. Review G4 chronology in
   [CONTROLLED_G9_ADMISSION_V1.md](docs/project/CONTROLLED_G9_ADMISSION_V1.md) and the
   [015 integrity index](artifacts/evidence/stage4/EXP-STAGE4-SF002-015.integrity-index-v1.json).
   The attempt-017 archive SHA-256 is `82d0b62ed33d0fb6ebc2924233a70fb1a1ae2e2385b8e374d06c931456eefb31`.
   The 015 and 017 raw bundles are preserved outside this repository. No attempt 018 is
   authorized.

## Historical Claims

Charts under `assets/training/` are archived upstream illustrations. They are not current
team measurements. The inherited 54%/68%/82% resolution and 0.481/0.601/0.729 reward
figures were not reproduced by this continuation. Predetermined Stage 13 profiles,
including 100% resolution, 18-second TTR, and 0.918 reward, are not empirical outcomes.
The required current research scope is GAI + RL; G10/G11 recommender work remains
historical optional research and OUT_OF_SCOPE.

## Package and Checks

The [submission manifest](artifacts/SUBMISSION_MANIFEST.json) lists selected tracked
files with their checkout-byte SHA-256 hashes and sizes. The
[submission summary](artifacts/SUBMISSION_SUMMARY.md) records the declared gate statuses.
Hashes establish file integrity, not scientific validity or certification. Run the
focused local checks from the repository root:

```powershell
python -m pytest -q tests/test_claim_integrity.py
python -m pytest -q tests/test_stage14_demo_safety.py
python -m pytest -q tests/test_stage15_submission_package.py
```

The report and package are review artifacts, not evidence that G0-G15 are all passed.
The live G4/G9 tracks are frozen for this continuation. No model training, Test or
Leaderboard evaluation, cluster mutation, or public deployment is part of this review.
