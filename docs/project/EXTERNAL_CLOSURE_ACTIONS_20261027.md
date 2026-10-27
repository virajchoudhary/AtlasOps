# External closure actions

Date: 27 October 2026. Scope: PR #235 software closure, before the owner's
October 27 Bug Lab Run Plan. This record contains no secret values and authorizes
no infrastructure, tunnel, model experiment, deployment, or submission.

## Credential remediation

Removal from current source and a passing secret scan do not revoke a credential.
Git history and negative evidence remain intact. Do not retrieve or test exposed
values from history.

| Item | Current evidence | Outstanding action | Required receipt |
| --- | --- | --- | --- |
| Grafana credential formerly in `CLAUDE.md` | Current source is redacted; history is retained; rotation UNVERIFIED | Resource owner rotates or revokes it in the authorized secret store and invalidates old sessions if applicable | Provider/resource identity, timestamp, operator, and redacted completion status |
| Inherited Argo CD credential | Active defaults removed; external validity UNKNOWN and NOT TESTED | Actual resource owner revokes or rotates any still-active corresponding credential | Redacted revocation/rotation confirmation |
| Previously exposed Azure OpenAI and OpenRouter keys | Exposure recorded in the completion checklist; rotation UNVERIFIED | Account owners revoke or rotate affected keys, update authorized secret stores, and review usage | Redacted provider confirmation and usage-review disposition |
| Previously exposed developer-local Anthropic and Hugging Face tokens | Exposure recorded in `SECURITY_REMEDIATION.md`; rotation UNVERIFIED | Account owners revoke or rotate affected tokens if active | Redacted provider confirmation |
| Historical public audit HMAC fallback | Removed from active code; old signatures remain strongly untrusted | Generate a unique private secret for any newly authorized runtime | Secret-store reference only, never the value |

No provider-console access, credential use, rotation, or secret-store mutation
was performed in this closure task.

## GCP resource and billing reconciliation

The May hackathon inventory in [CLAUDE.md](../../CLAUDE.md) is historical.
Its former LIVE label is not current evidence. Resource ownership, existence,
health, public exposure, and billing attachment are UNVERIFIED.
No inference that a resource is stopped, deleted, free, or still running is valid.

The authorized GCP project and billing-account owner must obtain a dated,
read-only console inventory of the named historical project and reconcile:

- GKE clusters and node pools, Compute Engine instances and disks, Cloud SQL,
  load balancers, forwarding rules, reserved addresses, and persistent storage.
- Pub/Sub, Artifact Registry, logging and monitoring retention, and any other
  resources visible in that project's actual inventory.
- Billing attachment, accrued charges, credits, budgets, alerts, and usage
  after the last known activity. Credits do not prove zero spend.
- Ownership and security of any surviving Grafana, Prometheus, Alertmanager,
  Jaeger, Argo CD, or application endpoint.

Retain redacted resource identities, observation times, billing periods, amounts
and currency, and the owner's disposition. Any shutdown, deletion, exposure
change, billing change, or new provisioning is a separate consequential action.
This task does not perform or authorize it.

## Run-plan prerequisites

Repository software acceptance is distinct from an executable experimental host.
The new Bug Lab Run Plan must name its exact source, target, operator, access
boundary, resource budget, model identity if used, evidence location, and cleanup
limits before execution. Kaggle, ngrok, Docker infrastructure, EKS, and new model
experiments were not started for this closure.

The following remain external or scientific dependencies:

- Owner-selected host and peer/operator acceptance, with fresh resource and
  telemetry qualification. Historical GCP notes and green container CI are not
  host qualification.
- G4 causal recovery and cleanup, with source/protocol reopening and a new
  exact-action permit. Earlier negative, inconclusive, aborted and marker-only
  observations remain classified as recorded.
- G6 and G8 matched incident evidence after their prerequisites, not diagnostic
  scores alone.
- Formal G9 accepted environmental-learning evidence and artifact provenance.
  Exploratory CPU/GPU right-sizing results are NOT_APPLICABLE to formal G9 and
  do not reopen it.
- G12's accepted policy and integrated episode; G13's accepted comparable arms,
  population, clocks, formulas, access and independent measurement.
- G14 peer deployment/security acceptance and G15 scientific review plus an
  actual owner submission receipt. Visual parity remains partial.

G4 and G9 remain NOT_PASSED/FROZEN; G6, G8 and G12 retain empirical-evidence
gaps; G13 remains REOPENED; G14 and G15 retain PARTIAL/NOT_CERTIFIED.
G7's bounded artifact/reload PASS and G10/G11's OUT_OF_SCOPE status are unchanged.
