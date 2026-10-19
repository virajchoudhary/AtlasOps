# Continue G13 measurement preparation

This handoff describes offline software safeguards based on accepted main
`b77bf7411f34b7faa8dd4dcc440cb78b5a67c30c`. It does not reopen or certify G13.
The [completion matrix](COMPLETION_CHECKLIST.md) remains authoritative.
The current [measurement proposal](PROJECT_CLOSURE_EXECUTION_PROPOSAL_V1.md)
is not an approved clock, adjudication, or campaign contract.

## Completed software

- Unknown lifecycle names remain unscorable failures with original names and
  raw references. They cannot silently turn into recovered episodes.
- `telemetry_loss` remains pending independent adjudication rather than an
  automatic infrastructure exclusion.
- Recovery and unresolved observation endpoints are retained only after
  delivered alert and action, and before the first cleanup boundary.
- Replay preserves eligible denominators and does not label a resolved-only
  TTR mean as a population mean.
- Lineage consistency explicitly describes descriptor metadata. It does not
  attest source authenticity, served weights, or scientific comparability.

Worker verification reports eight expected failing regressions before repair
and 268 assigned tests passing after. Independent source review accepted the
bounded safeguards and separately ran 177 measurement, replay and lineage tests.
These are software/model-free results, not incident observations.
Exact-head CI and published PR acceptance are separate prerequisites for landing.

## Reproduce the checks

Use the exact reviewed checkout and its Python 3.11 or 3.12 environment.
On Windows, use the Python 3.12 development lock from the repository.
In a new peer checkout, create the development environment before the tests:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip --isolated install --index-url https://pypi.org/simple -r requirements/dev-win-py312.lock
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
```

This dependency installation needs network access. Do not install training
extras or download weights. In this reviewed owner-local worktree, reuse the
existing canonical environment instead of creating another one:

```powershell
$python = 'C:\AtlasOps\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "The selected development interpreter is unavailable." }
```

After selecting the interpreter, run from the reviewed repository root:

```powershell
& $python -B -m pytest tests/test_candidate_measurement.py tests/test_candidate_replay.py tests/test_candidate_lineage.py tests/test_candidate_adapters.py -q
& $python -B -m ruff check bench/candidate_measurement.py bench/candidate_replay.py bench/candidate_lineage.py --select E9,F63,F7,F821,F401,F841
```

The optional broader Ruff rules have existing findings outside this change.
Do not weaken checks or reformat unrelated regions to conceal them.
Use a new writable pytest temp directory when the host's default temp path is
unavailable. Never reuse a frozen evidence directory.

## Missing contract and evidence

All TTR durations, censor durations and replay TTR means remain null and
unavailable. The present schema names a timestamp field but has no conditional
clock-attestation path. Endpoint metadata is retained; a comparable duration
is not fabricated from it.

The project lead must freeze the following before a new measurement executor:

- Clock identities, precision, offsets, skew bounds and wall/monotonic correlation.
- Alert origin, complete observation coverage, cleanup boundary and censoring rules.
- Outcome-independent infrastructure invalidation and the blinded adjudication input.
- Population membership, seeds, exact arm order, null rules and comparison formulas.
- Common source/scorer rules, independent serving attestation and third-arm lineage.
- Permitted Train/Validation scope, separate held-out access and reattempt authority.

An independent reviewer must recompute the final declared metrics from
authenticated raw observations under that approved contract. The existing
candidate replay recomputes its input using its own scorer; that is not a
second independent scientific implementation.
No approved formula, authentic clock evidence, adjudicator, accepted GRPO third
arm, or held-out access is supplied by this handoff.

## Owner decisions and next action

The project lead owns measurement and access approval. The selected host owner
supplies resource and clock qualification. The operator supplies exact-action
incident approvals. An independent reviewer owns raw-evidence recomputation.
No peer name or approval is assumed.

First review these non-empirical regressions. Then freeze a versioned contract
without overwriting the historical proposal or evidence. Implement and test the
contract-bound attestation and independent recomputation path before requesting
any campaign or held-out access. An accepted G9 policy and G12 integration remain
dependencies for the third arm.

Keep all software checks model-free. Do not start services, load pretrained
weights, train, inject faults, remediate, use Final-Test or deploy from this guide.
Stop on unknown event/source identity, missing observation coverage, contradictory
verifier fields, unavailable clocks, missing authority or source drift.
Do not exclude a failure, replace an episode, widen a budget or retry a research
run without a fresh approved decision. G13 remains REOPENED and NOT_CERTIFIED.
