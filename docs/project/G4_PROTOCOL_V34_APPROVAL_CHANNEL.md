# G4 prospective v3.4 approval channel

Status: prospective implementation candidate, **not an executed or certified G4 result**.
Marker: `G4-RECOVERY-V3.4-2026-09-26` (`g4-recovery-profile-v3.4`).
Scope: Stage 4 host runner approval transport and clean-checkout credentials only.
The `G4-RECOVERY-V3.3-2026-09-16` declaration and all historical attempts remain frozen.

## Reason for a new profile

The Stage 4 runner calls `handle_incident` inside its host Python process. Its
pending `ApprovalGate` is process-local. The in-cluster coordinator's authenticated
`POST /approve` and `GET /approval/pending` operate on a different gate, even
when port-forwarded to the host. The runner also forced a two-second timeout,
and missing checkout-local secrets were replaced with demo literals. A decision
at the in-cluster endpoint could not release the runner's waiting incident.
These are platform/control-path faults, not evidence of a model outcome.

## Prospective contract

- Before cluster contact or attempt reservation, the runner requires
  `ARGOCD_PASS`, `ATLASOPS_AUDIT_SECRET`, `ATLASOPS_API_KEY`, and
  `ALERTMANAGER_WEBHOOK_SECRET`. Each is supplied explicitly as an environment
  variable or read from the matching `.secret` file in an absolute
  `ATLASOPS_STAGE4_SECRET_DIR` outside the checkout. If both sources exist
  they must agree. Missing, empty, unreadable, or conflicting values stop the
  run. No checked-in or demo secret is a fallback.
- The runner starts an authenticated HTTP listener on an ephemeral
  `127.0.0.1` port in the **same event loop and process** as `handle_incident`.
  Only `POST /approve` and `GET /approval/pending` are exposed. The listener
  must start before preflight, reservation, or injection; a startup failure
  stops the run. It shuts down when the run ends.
- The local operator reads the listener address printed by the runner and
  supplies `X-AtlasOps-Key` to inspect the pending request and post the exact
  token, `approved` or `rejected`, and their identity. The notification also
  carries this loopback address; an in-cluster URL is **not** the host runner
  callback. The operator must be on the runner host (or use a separately
  reviewed secure access path). Do not expose this listener on a public
  interface or put the key in a URL, command line, log, or evidence bundle.
  The shared API key authenticates access; the `approved_by` name is
  operator-supplied, not independent personal identity attestation.
- The P1 gate waits up to 300 seconds for the actual authenticated decision.
  Only `approved` permits remediation. Rejection, timeout, missing decision,
  cancellation, and stale or duplicate callbacks fail closed. Restart
  invalidates in-memory tokens; an interrupted incident cannot be resumed or
  retrospectively approved. The existing persisted approval outcome and
  authoritative environment-verifier criteria remain unchanged. A hard
  process restart after fault injection does not prove cleanup; the existing
  interruption and poisoned-environment recovery controls still govern that
  case.
- The profile's canonical fingerprint now includes the transport, timeout,
  authentication, and restart contract. v3.3 and earlier fingerprints and
  attempt accounting remain historical. A future run must use a clean
  approved `main` checkout and pass the full live preflight before reservation.

## Operator preparation (no execution in this change)

Create or provision secrets in a protected directory **outside** the clean
checkout, for example using `scripts/generate_runtime_secrets.py --output-dir`
with an absolute private path. Set `ATLASOPS_STAGE4_SECRET_DIR` to that path,
or explicitly provide all four environment variables. Verify separately
that the credentials match the deployed coordinator/Argo CD secrets. Never
commit, print, or paste their values. The local API key protects the host
listener; the in-cluster service is still independent.

The synthetic subprocess tests cover both decisions, HTTP authentication,
the unrelated coordinator process, timeout, replay, and process restart.
They do **not** establish cluster reachability, model performance, actual
human approval, remediation safety under a real incident, or G4 closure.
Attempt `EXP-STAGE4-SF002-015` is **unreserved**; this revision stops before
live execution and must not change the negative/interrupted record for 009-014.
G4 remains `NOT_PASSED`.
