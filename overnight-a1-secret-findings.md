# Overnight A1 — Worktree-Wide Secret-Pattern Sweep Findings

Date: 2026-08-24 (overnight session)
Scope: all tracked files in this worktree (`git ls-files`, 204 files) on branch
`overnight/atlasops-hardening-20260824` at `e94f1e4` (Merge PR #30).
Method: automated pattern sweep (public IPv4s excluding RFC1918/RFC5737/loopback/multicast,
credential-assignment shapes, PEM/AWS/Slack/Discord/GCP-SA markers,
credential-bearing connection strings, bcrypt hashes) plus manual triage of every hit.
No secret values are reproduced in this report.

## Prior state (verified)

- `CLAUDE.md` had already been redacted by the earlier A1 pass before this session:
  all seven live GKE LoadBalancer IPs removed; env-var supply pattern
  (`GRAFANA_URL`, `GRAFANA_ADMIN_USER`, `GRAFANA_ADMIN_PASSWORD`, `PROMETHEUS_URL`,
  `ALERTMANAGER_URL`, `JAEGER_UI_URL`, `ARGOCD_URL`) added with a security note.

## Findings requiring action (found this session)

| # | Location | What was there | Disposition |
|---|---|---|---|
| 1 | `infra/setup_mi300x.sh` line ~53 | Residual live LoadBalancer IP (`136.119.x.x`, the former Grafana endpoint) hardcoded as `PROMETHEUS_URL=` | **Redacted this session** — replaced with `$env:PROMETHEUS_URL` env-var supply pattern + comment |
| 2 | `static/index.html` line ~1304 | Live Grafana LB IP embedded as demo-page link target (`http://136.119.x.x`) | **Redacted this session** — link neutralized to a non-functional placeholder |
| 3 | `static/index.html` line ~1308 | Live Online Boutique frontend LB IP embedded as demo-page link target (`http://34.132.x.x`) | **Redacted this session** — link neutralized to a non-functional placeholder |

## Triage of remaining sweep hits (no action required)

- **Env-var reads / variable names** (`os.getenv("...API_KEY")`, `ATLASOPS_API_KEY`,
  `ARGOCD_PASS`, etc. across `agents/`, `app.py`, scripts): correct fail-closed pattern;
  no literal values committed.
- **Test placeholders** (`test-placeholder-*`, `fw_xxx`, `sk_xxx`, `apr-123`,
  `TestArgoPassword987!` inside test fixtures): obviously synthetic; safe.
- **ML false positives** (`tokenizer`, `token_accuracy`, "Tokenizing train dataset" in
  training docs/evidence): substring collisions with `token`, not credentials.
- **`artifacts/evidence/stage3/acceptance_report.json`** flagged IP-looking token
  `5.15.167.4`: it is part of the WSL2 kernel version string
  (`5.15.167.4-microsoft-standard-WSL2`), not an address.
- **Internal cluster DNS endpoints** in docs (`*.svc.cluster.local`) and Kind node IPs
  (`172.20.0.4`, `10.244.0.x`) in evidence: private/non-routable, no exposure.
- **Secret-file *name* references** (`secrets/atlasops-api-key.secret` etc. in operator
  guides): names only; values are generated locally by
  `scripts/generate_runtime_secrets.py` and gitignored.

No PEM keys, AWS keys, Slack/Discord webhook URLs, GCP service-account JSON,
bcrypt hashes, or credential-bearing connection strings are present in any tracked file.

## Rotation / history-cleanup items for the human (cannot be fixed by editing HEAD)

1. **Grafana admin credential rotation (recommended, high priority).**
   The working tree is clean, but git **history** still contains the old CLAUDE.md
   content that included the Grafana endpoint together with its admin username and
   password (`cloudsre-admin`). History touches: initial commit `87de5f4`, `7b1f710`,
   and every commit up to the A1 redaction (the string survives until this branch's
   uncommitted edit lands).
   → Rotate the Grafana admin password now; it must be considered public.
2. **LoadBalancer IP exposure history (low severity, informational).**
   Seven live IPs appear across 1–8 historical commits each
   (Grafana `136.119.x.x`; Boutique `34.132.x.x`; Argo CD `34.122.x.x`;
   Cloud SQL `34.60.x.x`; Prometheus `34.31.x.x`; Alertmanager `34.135.x.x`;
   Jaeger `104.198.x.x`). They are ephemeral endpoints, but if any cluster or
   project is still up:
   - confirm none of these services are still world-open (unauthenticated Prometheus/
     Alertmanager/Jaeger UIs were exposed in May per repo notes);
   - prefer releasing/recreating the LoadBalancers so the documented addresses die;
   - Cloud SQL: verify `34.60.x.x` is not authorized-network-wide open (0.0.0.0/0)
     and rotate the DB user password if it ever appeared outside Git.
3. **History rewrite decision (explicit authorization required).**
   Per AGENTS.md, history rewriting needs explicit human authorization. Options:
   (a) accept rotation-only (simplest, recommended once passwords are rotated), or
   (b) run BFG/git-filter-repo to purge the credential strings, then coordinate a
   force-push window. Do NOT attempt (b) without coordinator sign-off.
4. **Preventive guardrail (follow-up suggestion).**
   Consider adding a lightweight secret-pattern scanner to CI (similar spirit to
   `scripts/check_doc_consistency.py` added this session) so new IP/credential
   residues fail CI instead of relying on periodic manual sweeps.

## Verification

Post-redaction re-run of the same sweep over tracked files returns zero
public-IP findings and zero credential-value findings (only the false-positive
classes listed above remain).
