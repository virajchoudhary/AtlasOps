# Mac runtime acceptance reconciliation

This note reconciles [PR #241](https://github.com/virajchoudhary/AtlasOps/pull/241)
with protected main `ea797b002c8133d0ab67671c328101e16ebef7d1`,
[PR #248](https://github.com/virajchoudhary/AtlasOps/pull/248), and
[PR #247](https://github.com/virajchoudhary/AtlasOps/pull/247).
It records software integration and corrections to interpretation, not a new Mac run.
Final accepted heads, CI, and package hashes belong in the exact-main release receipt.

## Preserved observations

PR #241 source `44f95f71bad7a4306be27d6eeb5a0475db6a646c` supplies all
35 original files under `artifacts/evidence/atlasops-mac-runtime-v1/` and the
[original acceptance report](ATLASOPS_MAC_RUNTIME_ACCEPTANCE_V1.md).
The original report, failed attempts, raw outputs, timestamps, and classifications
remain unchanged. Regression tests pin all 36 original Git blob identities.
The raw evidence directory disables Git text conversion so every platform retains
the original Git bytes. The earlier Windows-converted export remains preserved
with the closure receipts. No evidence driver is rerun.

The original report describes `fafe1eb` plus its entrypoint fix.
Its Mac and Kubernetes observations do not qualify the newer coordinator,
infrastructure, dependency lock, or disposable-CI implementation.

The original [50-record audit](../../artifacts/evidence/atlasops-mac-runtime-v1/coordinator_audit_log.jsonl)
contains 10 `kubectl_get` records and 4 `kubectl_scale` records, rather than the
report's 8 and 6. The four scale records represent two call/result pairs.
The prose's two scale operations agrees with the raw data.
Hash-chain adjacency does not verify HMAC signatures without the owner key.
No owner audit key is accessed for reconciliation.

T5 preserves eight rule-engine resolutions. T7 preserves eight verified
demonstration resolutions, with one model-authored final patch and seven
rule-engine patches. Failed model attempts remain visible.
These results are not model-only success or scientific improvement.

## Integrated software changes

PR #248 supplies the maintained container entrypoint and its native and
amd64/arm64 named-volume regressions. The entrypoint selects the final
`--workdir`, including explicit overrides, before checking the ownership marker.
It preserves first startup, owned-workspace resume, and explicit process commands.
Existing marker, journal, writer-lock, and path-integrity checks still apply.
Unknown contents are not adopted, and interrupted actions remain unverified.
Old approvals cannot replay after restart. The Python CLI retains its existing API.

The shared deployment-unavailable rule uses `severity: critical`.
This preserves the validated PR #241 defense for classic coordinator mode.
In the current local operational profile, `ATLASOPS_OPERATIONAL_MODE=1`
already requires approval for every non-manual proposed action.
Critical severity is therefore not the only current approval trigger.
The renderer selects the `atlasops-boutique` namespace. The source rule's
historical `namespace="default"` selectors are not rewritten to match an old override.

The severity regression checks the actual parsed rule. The selected review
inventory includes the original Mac records, reconciliation, container entrypoint,
runtime workflow, and local infrastructure. The exact-source archive includes
every regular tracked file, not just the selected inventory.
Passing software checks do not constitute an owner-device acceptance run.

The Windows post-merge runtime job exposed a polling-test defect: one transient
two-second read timeout escaped the existing ten-second incident-state wait.
The polling helper now retains that bounded wait after a read timeout.
It still fails at the original deadline and does not hide a stopped server.
All incident, approval, rejection, history, and verifier assertions remain.

## Current container command contract

These commands describe a separately authorized owner run. Reconciliation does not
start a container, model, proxy, or cluster. See the
[local runtime guide](BUG_LAB_LOCAL_RUNTIME.md) for the maintained startup contract.

The image's default command is `--agents llm`. Override arguments directly:

```bash
docker run --rm -p 127.0.0.1:8790:8790 \
  atlasops-buglab:local --agents rules
```

Model mode retains the existing external env file:

```bash
docker run --rm -p 127.0.0.1:8790:8790 \
  --env-file ~/atlasops-local/buglab.env atlasops-buglab:local
```

An explicit command remains supported. For a model proxy, its separate process
and configuration stay with the runtime owner:

```bash
docker run --rm -p 127.0.0.1:8000:8000 \
  --env-file ~/atlasops-local/proxy.env \
  -v "$PWD/scripts/local_llm_proxy.py:/app/proxy.py:ro" \
  --entrypoint python atlasops-buglab:local \
  -m uvicorn proxy:app --host 0.0.0.0 --port 8000
```

Historical runbooks retain their original command syntax as evidence.
Persist an owned workdir on a suitable volume for real container restarts.
An ephemeral tmpfs is not evidence retention across container restart.
Admin interfaces and the proxy remain loopback-only.

## Acceptance still required

The Mac owner must qualify the final merged SHA with the existing runtime
configuration under separate authorization. Required observations include
first startup, restart with retained evidence, a custom workdir, fresh browser
telemetry, exact-action approvals, rejection, expiry, and old-receipt refusal.
Real endpoint identity, authentication, replies, deadlines, outage fallback
attribution, and endpoint rotation still need final-source smoke tests.

The earlier crashloops, CPU contention, empty Jaeger services, absent node metrics,
and missing objective Kubernetes recovery remain historical findings.
New instrumentation and disposable CI require their own source-bound evidence.
A deterministic model fixture can exercise real Kubernetes software without
establishing real-model or final-device acceptance.

PR #241 is superseded only after its preserved report and records, validated
restart and severity fixes, regressions, and this explanation are on accepted main.
Closing the PR does not delete its branch, commits, reviews, or unsuccessful runs.

No credentials, Mac overrides, Kaggle notebook, ngrok route, EKS deployment,
model experiment, or scientific gate status changes in this reconciliation.
G4 and G9 remain NOT_PASSED and frozen. The project remains NOT_CERTIFIED.
External rotation and billing obligations remain in
[the owner action register](EXTERNAL_CLOSURE_ACTIONS_20261027.md).
