# Stage 1A security configuration remediation

Date: 2026-08-12
Repair PR: `fix: harden runtime security configuration` from `fix/security-config-baseline`

This record contains no credential or signing-secret values. Both findings were
inherited unchanged from the frozen upstream baseline
`bf9bd197c9f4a05ae55ade254802a9eef1a74356`.

## Finding A: inherited Argo CD credential and configuration defaults

The inherited Argo CD wrapper contained public runtime defaults for its endpoint,
username, and password, downgraded configured HTTPS to HTTP, disabled certificate
verification, and globally suppressed insecure-request warnings.

The current repair removes all active endpoint, username, and password defaults.
Real Argo CD operations now require explicit `ARGOCD_URL`, `ARGOCD_USER`, and
`ARGOCD_PASS` configuration and fail locally before HTTP when any required value is
missing. `ARGOCD_VERIFY_TLS` is optional and defaults to `true`; only an explicit
operator setting may disable verification. Configured HTTPS is never downgraded.

The historical external credential status is **UNKNOWN** and it was **NOT TESTED**.
Public Git history may retain the historical value. Deleting it from active source
does not revoke it, and this repository does not establish ownership of the referenced
infrastructure. The actual resource owner must revoke or rotate any corresponding
credential if it remains active. Git history was not rewritten.

## Finding B: inherited public audit HMAC fallback

The inherited audit module substituted a fixed repository-known HMAC key when
`ATLASOPS_AUDIT_SECRET` was absent. Anyone with the public source could reproduce
signatures made with that key, so records signed through the fallback cannot establish
strong tamper-evident authenticity.

The current repair removes the fixed fallback. Module imports remain safe, explicit
`AuditLog` construction remains available for isolated tests, and real coordinator or
agent execution requires a non-blank `ATLASOPS_AUDIT_SECRET` before model or tool
activity. Future environments must generate and store a private, unique secret through
an appropriate external secret-management mechanism.

Historical logs are not deleted or rewritten. Records signed with the former public
fallback remain **strongly untrusted for authenticity** and must not be represented as
cryptographically trustworthy.

## Developer-local token exposure note

Developer-local `ANTHROPIC_AUTH_TOKEN` and `HF_TOKEN` values previously appeared in
diagnostic output. Their owner was instructed to rotate them if active. No values were
inspected, copied, or recorded in this remediation.
