"""Presentation startup checks, never operational or scientific acceptance."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from demo.rehearsal import rehearsal_catalog
from ui_read_model import ROOT, catalog


class _Assets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paths: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script" and attributes.get("src"):
            self.paths.append(attributes["src"])
        if (
            tag == "link" and attributes.get("rel") in {"stylesheet", "modulepreload"}
            and attributes.get("href")
        ):
            self.paths.append(attributes["href"])


def check_readiness(dist: Path | None = None) -> dict:
    """Check files consumed by the demo; do not start services or issue requests."""
    dist = (dist or ROOT / "frontend/dist").resolve()
    errors: list[str] = []
    checks: dict[str, bool] = {}
    try:
        index = (dist / "index.html").read_text(encoding="utf-8")
        parser = _Assets()
        parser.feed(index)
        if not parser.paths or not any(urlsplit(path).path.endswith(".js") for path in parser.paths):
            raise ValueError("Built index contains no JavaScript bundle")
        for path in parser.paths:
            url = urlsplit(path)
            asset_path = unquote(url.path).removeprefix("./").lstrip("/")
            asset = (dist / asset_path).resolve()
            if (
                url.scheme or url.netloc or not asset_path.startswith("assets/")
                or not asset.is_relative_to(dist) or not asset.is_file()
                or asset.stat().st_size == 0
            ):
                raise ValueError("Built index references a missing or unsupported asset")
        checks["frontend_assets"] = True
    except (OSError, ValueError, UnicodeError):
        checks["frontend_assets"] = False
        errors.append("Frontend build unavailable or incomplete. Run npm run build --prefix frontend.")
    try:
        snapshot = catalog()
        if len(snapshot["gates"]) != 16:
            raise ValueError("Incomplete gate snapshot")
        checks["evidence_projection"] = True
    except (OSError, ValueError, KeyError, TypeError):
        checks["evidence_projection"] = False
        errors.append("Repository evidence projection unavailable; restore the matching checkout.")
    try:
        fixtures = rehearsal_catalog()
        if len(fixtures["scenarios"]) != 2:
            raise ValueError("Incomplete rehearsal fixtures")
        checks["rehearsal_fixtures"] = True
    except (OSError, ValueError, KeyError, TypeError):
        checks["rehearsal_fixtures"] = False
        errors.append("Rehearsal fixtures unavailable; inspect the frozen scenario/policy references.")
    return {
        "local_presentation_ready": not errors,
        "scope": "Local file preflight only; not HTTP, hosted deployment, live agent, or gate acceptance",
        "mode": "read-only",
        "checks": checks,
        "errors": errors,
    }
