"""No live endpoint or credential may be committed to this repository.

CLAUDE.md previously published a Grafana admin password and the public IPs of
seven internet-exposed services; static/index.html linked two of them. Those
values remain in this repository's public Git history and must be rotated at
the source — removing them from the working tree does not revoke them.

These checks exist to stop the same class of value being reintroduced, and are
written so that the guard itself contains no plaintext secret.
"""

from __future__ import annotations

class TestNoPublishedCredentialsOrEndpoints:
    """Endpoints and credentials must come from the environment, never source.

    CLAUDE.md previously published a Grafana admin password and the public IPs of
    seven internet-exposed services; static/index.html linked two of them.

    These checks deliberately contain **no plaintext secret**. An earlier version
    listed the seven IPs and the credential as literals to search for, which meant
    the guard against publishing them republished them on every commit and tripped
    secret scanners. Two changes remove that need:

    * IPs are matched by *classification* rather than by value — any globally
      routable address hardcoded in a tracked source fails, which is a stronger
      invariant than the original seven-literal list and needs no literals.
    * The credential is matched by SHA-256 digest, so the check knows the value
      without the repository containing it.
    """

    _EXCLUDED_DIRS = (".git", ".venv", "node_modules", "__pycache__")
    # SHA-256 of the inherited Grafana admin password. Storing the digest lets
    # this test detect reintroduction without republishing the credential.
    _CREDENTIAL_DIGEST = "8ad7a46b5c68250f3ccd56f6b4eab19d8ba621fd10d437d479e7c1d0632a994b"

    def _sources(self):
        """Every file git tracks, plus untracked sources about to be added."""
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        try:
            listed = subprocess.run(
                ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
            ).stdout.split()
        except (subprocess.CalledProcessError, FileNotFoundError):
            listed = []  # exported copy with no .git; the rglob below still applies
        candidates = {root / name for name in listed}
        for pattern in ("*.py", "*.md", "*.sh", "*.html", "*.yaml", "*.yml", "*.json"):
            candidates.update(root.rglob(pattern))

        for path in sorted(candidates):
            if not path.is_file():
                continue
            if any(f"/{d}/" in f"/{path.relative_to(root)}" for d in self._EXCLUDED_DIRS):
                continue
            try:
                yield path.relative_to(root), path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

    def test_no_globally_routable_ip_is_hardcoded_in_sources(self):
        """Any public IP in a tracked source is an endpoint that should be config.

        Private, loopback, link-local and RFC 5737 documentation ranges are fine —
        those are how tests and local defaults are meant to be written.
        """
        import ipaddress
        import re

        # Reject dotted quads that are part of a longer version token: a kernel
        # string like "5.15.167.4-microsoft-standard-WSL2" parses as a valid,
        # globally routable address but is not an endpoint. Requiring that the
        # quad is not adjacent to word/dot/dash characters separates the two,
        # while still matching real endpoints such as "http://a.b.c.d:9090".
        octet = re.compile(r"(?<![\w.-])(?:\d{1,3}\.){3}\d{1,3}(?![\w.-])")
        # No self-exclusion: this file no longer contains the literals it guards,
        # so it is scanned like any other source.
        offenders, scanned = [], 0
        for relpath, text in self._sources():
            scanned += 1
            for candidate in set(octet.findall(text)):
                try:
                    address = ipaddress.ip_address(candidate)
                except ValueError:
                    continue  # a version string, not an address
                if address.is_global:
                    offenders.append(f"{relpath}: {candidate}")

        assert scanned > 100, f"scan covered only {scanned} files — it is not proving anything"
        assert sorted(offenders) == [], (
            "Globally routable IPs are hardcoded in tracked sources. Move them to "
            "environment variables (see .env.example):\n  " + "\n  ".join(sorted(offenders))
        )

    def test_grafana_credential_is_not_published(self):
        """Matched by digest so this file never contains the credential itself."""
        import hashlib
        import re

        token = re.compile(r"[A-Za-z0-9._-]+")
        offenders, scanned = [], 0
        for relpath, text in self._sources():
            scanned += 1
            for word in set(token.findall(text)):
                if hashlib.sha256(word.encode()).hexdigest() == self._CREDENTIAL_DIGEST:
                    offenders.append(str(relpath))

        assert scanned > 100, f"scan covered only {scanned} files — it is not proving anything"
        assert sorted(set(offenders)) == [], (
            f"The inherited Grafana credential appears in: {sorted(set(offenders))}. "
            "It must be supplied via ARGOCD_PASS/secret store, never committed."
        )
