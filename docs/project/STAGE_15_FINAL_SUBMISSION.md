# Stage 15: Report, Package, and Submit (Gate G15)

Stage 15 is **PARTIAL**. The report and asset package exist, but the full research pipeline is not certified or submitted. The authoritative current gate inventory is in [MASTER_PIPELINE_STATUS.md](MASTER_PIPELINE_STATUS.md); executable behavior and preserved raw evidence outrank any summary.

## Current package

- [Technical report draft](../AtlasOps_Technical_Report.md): separates implementation, historical negative evidence, mock outputs, and absent empirical results.
- [Submission manifest](../../artifacts/SUBMISSION_MANIFEST.json) and [summary](../../artifacts/SUBMISSION_SUMMARY.md): generated asset hashes and declared G0-G15 statuses. `NOT_CERTIFIED` is the only supported package status while empirical gates remain open.
- [Package generator](../../scripts/package_submission.py): hashes selected files and reads the master gate inventory. Hashes prove file integrity, not model quality, live safety, or gate closure. Tests write to isolated temporary directories.
- [Evidence-led presentation source](../slides.md) and its two local UI captures in `docs/media/`: describe the current continuation status without promoting upstream claims or historical mock output into measured results. The presentation source and captures are included in the asset inventory; a rendered deck is a review artifact, not scientific certification.

The checked-in manifest records **raw checkout bytes** and emits only each selected asset's SHA-256 and size. The generator selects paths from the Git index, excludes untracked and ignored files, and rejects paths resolving outside the repository through symlinks. Root and evidence-level `.gitattributes` pin the 28 G5 scenario YAML files, pre-009 Stage 4 records and selected run logs, Stage 3 acceptance report, Stage 7 corpus/config files, Stage 10 baseline, recovered workspace provenance JSON, and SFT template to CRLF checkout bytes across platforms; the 009-014 Stage 4 records and frozen mock archives retain their `-text` rules, and the recovery command record remains LF. These rules preserve the existing Git blobs and do not rewrite evidence contents. Verify the manifest after checkout with `python -m pytest -q tests/test_stage15_submission_package.py`; do not normalize evidence bytes to make a hash pass.

## Evidence needed for completion

1. A valid G4 golden incident with explicit P1 approval, objective resolution, and verified cleanup.
2. Real G6 baseline predictions and model provenance.
3. A completed G7 SFT checkpoint and real G8 evaluation.
4. Safe real G9 GRPO trajectories, a hashed checkpoint, and real evaluation.
5. Real G12 integrated execution and the complete G13 ablation/stress artifact matrix.
6. Target-specific G14 demo/deployment validation and final independent review.

The historic Stage 13 predetermined profiles and mock Stage 6/8/9 outputs are retained as historical artifacts, not recast as empirical findings. The generator does not emit unobserved resolution, TTR, or reward metrics.

**Gate G15 status: PARTIAL.** No 15/15 certification or submission claim is made.
