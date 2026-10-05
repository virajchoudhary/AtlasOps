# Stage 15: Report, Package, and Submit (Gate G15)

The presentation and reviewer package is prepared for review; full scientific
certification and external submission are separate and remain unestablished.
The [Master Pipeline inventory](MASTER_PIPELINE_STATUS.md) is authoritative for
gate status. G15 remains **PARTIAL**.

## Readiness Contract

`artifacts/SUBMISSION_MANIFEST.json` keeps `status: NOT_CERTIFIED` as the
scientific certification result and reports package completeness separately
with `package_ready` and `package_readiness`. `READY_FOR_REVIEW` means the
required current-facing review surfaces and compact canonical evidence are
tracked and byte-hashed, and local Markdown links resolve. If a required file
or local link is missing, the generated manifest reports `INCOMPLETE` and
lists the missing items. Neither state promotes an empirical gate or claims
that an outside party has received the submission.

The generator records a deterministic SHA-256 over sorted asset path, content
hash, and byte-size rows. Its timestamp is informational and is excluded from
that digest. Each asset hash is computed from checkout bytes selected from the
Git index; untracked and ignored files are excluded. Path traversal through a
symbolic link or reparse point is rejected. The inventory does not rewrite
line endings or evidence bytes. Root and evidence `.gitattributes` also pin the
G4-015 integrity index and MIT `LICENSE` to their existing CRLF checkout bytes;
their evidence/license content is unchanged.

## Included Review Material

The allowlist includes the README, reviewer guide, technical report, slides,
experiment registry, evidence index, current gate/status documents, Stage 14
and Stage 15 material, and the read-only demo source. It also includes compact
canonical records for:

- The bounded v17 SFT result and independent reload.
- The matched Base-vs-SFT Validation result and independent recomputation.
- G4 attempt 015's integrity index and the 016/017 chronology references.
- The final controlled-G9 result and aligned diagnostic evidence.
- The historical mock archive classification and relevant software tests.

The [evidence index](../EVIDENCE_INDEX.md) identifies which full archives and
weights remain outside ordinary Git and records their documented hash anchors.
They are references, not bundled-byte claims. Model weights, private archives,
credentials, and other large generated bundles are not copied into this package.

## Verification

Run the focused package and current-truth checks, then check current-facing
Markdown links:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_stage15_submission_package.py tests/test_current_project_truth.py
& .\.venv\Scripts\python.exe -m scripts.check_submission_links
```

The Stage 15 tests recompute every selected asset hash from the current checkout,
check deterministic inventory hashing, preserve frozen evidence/EOL rules, and
verify that package readiness cannot replace `NOT_CERTIFIED`. The link checker
does not fetch external URLs or open linked evidence contents. Missing external
evidence can be documented as an external-evidence link with a SHA-256 in its
Markdown link title; Test/Leaderboard outcome paths are rejected without
checking their existence.

**Gate G15 status: PARTIAL.** The presentation/review package is ready when the
manifest reports `READY_FOR_REVIEW`; this does not mean the full G0-G15 research
pipeline is certified or that an external submission has occurred.
