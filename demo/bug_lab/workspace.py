"""The editable working copy of the Atlas Store, kept apart from the pristine release."""

from __future__ import annotations

import difflib
import hashlib
import os
import shutil
from pathlib import Path

from training.sft_provenance import has_redirecting_path_component

SITE_SOURCE = Path(__file__).resolve().parent / "site"
EDITABLE_FILES = ("index.html", "styles.css", "app.js", "products.json", "config.json")
MONITOR_TAG = '<script src="/lab/monitor.js"></script>'


class Workspace:
    """Bug injection and agent patches only ever write files inside ``root / "site"``."""

    def __init__(self, root: Path, source: Path = SITE_SOURCE) -> None:
        if has_redirecting_path_component(root):
            raise ValueError("Bug Lab workdir must not traverse a symbolic link or junction")
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise ValueError("Bug Lab workdir must be new or empty")
        self.root = root.resolve()
        self.source = source.resolve()
        self.site = self.root / "site"
        self.reset()

    def reset(self) -> None:
        if (
            has_redirecting_path_component(self.site)
            or not self.site.resolve().is_relative_to(self.root)
        ):
            raise ValueError("Bug Lab reset must stay inside its owned workspace")
        if not self.site.exists():
            shutil.copytree(self.source, self.site)
        else:
            for name in EDITABLE_FILES:
                self.write(name, self.pristine(name))

    def _editable(self, name: str) -> Path:
        if name not in EDITABLE_FILES:
            raise ValueError(f"{name!r} is not an editable store file")
        return self.site / name

    def read(self, name: str) -> str:
        return self._editable(name).read_text(encoding="utf-8")

    def pristine(self, name: str) -> str:
        if name not in EDITABLE_FILES:
            raise ValueError(f"{name!r} is not an editable store file")
        return (self.source / name).read_text(encoding="utf-8")

    def write(self, name: str, text: str) -> None:
        path = self._editable(name)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(text, encoding="utf-8", newline="")
        os.replace(temporary, path)

    def changed_files(self) -> list[str]:
        return [name for name in EDITABLE_FILES if self.read(name) != self.pristine(name)]

    def public_path(self, relative: str) -> Path | None:
        """Resolve a URL path to a served file, refusing anything outside the store."""
        if not relative or relative.endswith("/"):
            relative = f"{relative}index.html"
        parts = Path(relative).parts
        if any(part in {"..", ""} or part.startswith(".") for part in parts):
            return None
        candidate = (self.site / relative).resolve()
        if not candidate.is_relative_to(self.site) or not candidate.is_file():
            return None
        return candidate

    def asset_exists(self, relative: str) -> bool:
        return self.public_path(relative) is not None

    def files(self) -> list[str]:
        return sorted(
            path.relative_to(self.site).as_posix()
            for path in self.site.rglob("*")
            if path.is_file() and not path.name.startswith(".")
        )

    def version(self) -> str:
        digest = hashlib.sha256()
        for name in self.files():
            digest.update(name.encode("utf-8") + b"\0")
            digest.update((self.site / name).read_bytes() + b"\0")
        return digest.hexdigest()[:16]


def unified_diff(name: str, before: str, after: str) -> list[str]:
    return list(
        difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            fromfile=f"a/{name}", tofile=f"b/{name}", lineterm="", n=2,
        )
    )
