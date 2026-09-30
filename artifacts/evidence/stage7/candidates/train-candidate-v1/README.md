# Train Candidate v1 Review Bundle

**Synthetic review candidate. D3 PENDING. Training NOT AUTHORIZED.**

Construction Git commit:
`e802dd3a5522c30d3919952954e86827fa96caa1`.
The corpus contains 68 rows across the 16 frozen Train scenarios and 17 case
groups, with 17 examples per runtime role. All observations and named approval
examples are explicit simulations. The historical 64-row corpus is untouched
and rejected for training.

## Reproduce

Use an isolated checkout of the construction commit and the existing
non-training development dependencies. Choose an entirely new destination:

```text
python -m training.build_sft_candidate --output-dir <new-review-directory>
```

The command does not call runtime executors, models or training code. It refuses
existing paths and redirects, validates its source commit, and writes the
corpus plus adjacent manifest. No-clobber behavior preserves prior evidence
and partial failures. Serialize output-directory operations; the portable
path checks do not lock out hostile same-user parent-directory replacement.

Expected raw and canonical-LF corpus SHA-256:
`19606e4fec300f641c7c8b8a989497367a444a870d3491f225004c01df3ee5fd`.
Expected manifest raw SHA-256 at the construction commit:
`35c9fd63328ef1319f616f2a23a025be38ad99c594dcd8bb38b733e0ff44c67c`.
Checkouts at later commits may record a different construction SHA; exact
manifest reproduction requires the commit above. JSONL bytes replay identically
when the pinned construction files/configuration and Train membership match.

## Validate

In the reviewed repository, the non-model check is:

```text
python -m pytest tests/test_sft_candidate_admission.py tests/test_sft_candidate_builder.py
```

The stored-candidate test requires the bundle, checks the named source blobs,
replays the rows, recomputes hashes/distributions, and proves that the training
entrypoint refuses D3-pending input. CI fetches history for source validation.
This is a software/data-contract check, not a scientific approval or model run.

See `quality_audit.md`, the adjacent provenance manifest, and
`docs/project/G7_D3_CANDIDATE_REVIEW_V1.md` for independent review, exact
distributions and remaining lead decisions. Future remote acceptance is
defined in `docs/project/G7_SFT_PILOT_ACCEPTANCE_V1.md`; do not launch training
from this bundle.
