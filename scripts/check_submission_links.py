"""Check local links in current-facing review Markdown without opening evidence targets."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

CURRENT_MARKDOWN_PATHS = (
    "README.md",
    "JUDGES_START_HERE.md",
    "docs/AtlasOps_Technical_Report.md",
    "docs/slides.md",
    "docs/EXPERIMENT_REGISTRY.md",
    "docs/EVIDENCE_INDEX.md",
    "docs/project/MASTER_PIPELINE_STATUS.md",
    "docs/project/IMPLEMENTATION_STATUS.md",
    "docs/project/FINAL_PIPELINE_V22_STATUS.md",
    "docs/project/REPRODUCTION.md",
    "docs/project/STAGE_6_ZERO_SHOT_BASELINE.md",
    "docs/project/STAGE_7_SFT_DATA_AND_TRAINING.md",
    "docs/project/STAGE_8_SFT_EVALUATION.md",
    "docs/project/STAGE_9_ONLINE_GRPO.md",
    "docs/project/STAGE_12_INTEGRATED_PIPELINE.md",
    "docs/project/STAGE_13_FINAL_ABLATION_EVALUATION.md",
    "docs/project/STAGE_14_DEPLOY_FINAL_DEMO.md",
    "docs/project/STAGE_15_FINAL_SUBMISSION.md",
    "docs/project/BASE_SFT_VALIDATION_RESULT_V1.md",
    "docs/project/CONTROLLED_G9_ADMISSION_V1.md",
    "docs/project/CONTROLLED_G9_FINAL_NEGATIVE_V1.md",
    "docs/project/DEFERRED_RESEARCH_HANDOFF.md",
)

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(`+)(.*?)\1")
_LINK_RE = re.compile(
    r"(?P<image>!?)\[[^\]\n]*\]\("
    r"\s*(?P<target><[^>\n]+>|(?:\\.|[^\s)])+)"
    r"(?:\s+(?P<title>\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
)
_DOCUMENTED_EXTERNAL_EVIDENCE_TITLE = re.compile(
    r"external evidence\b.*\bsha-?256\s*[:=]\s*[0-9a-f]{64}\b",
    re.IGNORECASE,
)
_PROTECTED_RESULT_PART = re.compile(
    r"(?:^|[-_.])(?:test|leaderboard)(?:$|[-_.])",
    re.IGNORECASE,
)


def _without_code(text: str) -> list[tuple[int, str]]:
    """Return source lines with fenced and inline code removed."""
    result: list[tuple[int, str]] = []
    fence_char: str | None = None
    fence_length = 0
    for number, line in enumerate(text.splitlines(), 1):
        fence = _FENCE_RE.match(line)
        if fence is not None:
            marker = fence.group(1)
            if fence_char is None:
                fence_char, fence_length = marker[0], len(marker)
                continue
            if marker[0] == fence_char and len(marker) >= fence_length:
                fence_char = None
                fence_length = 0
                continue
        if fence_char is None:
            result.append((number, _INLINE_CODE_RE.sub("", line)))
    return result


def _is_protected_outcome_path(relative_path: PurePosixPath) -> bool:
    parts = tuple(part.lower() for part in relative_path.parts)
    if not any(part in {"artifacts", "outputs", "results"} for part in parts):
        return False
    return any(_PROTECTED_RESULT_PART.search(part) for part in parts)


def check_current_markdown_links(
    repo_root: Path,
    documents: tuple[str, ...] | list[str] = CURRENT_MARKDOWN_PATHS,
) -> list[str]:
    """Return local-link errors for the supplied current-facing Markdown files.

    External URLs are not fetched. Linked evidence files are only checked for
    path existence, never opened. Test/Leaderboard outcome paths are rejected
    before even checking their existence.
    """
    root = repo_root.resolve()
    errors: list[str] = []

    for document in documents:
        source_relative = PurePosixPath(document)
        source_path = root.joinpath(*source_relative.parts)
        if not source_path.is_file():
            errors.append(f"{document}: current-facing Markdown source is missing")
            continue
        try:
            content = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            errors.append(f"{document}: cannot read Markdown source ({error})")
            continue

        for line_number, line in _without_code(content):
            for match in _LINK_RE.finditer(line):
                raw_target = match.group("target")
                target = raw_target[1:-1] if raw_target.startswith("<") else raw_target
                target = target.replace(r"\ ", " ")
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue

                relative_target = unquote(parsed.path).replace("\\", "/")
                if relative_target.startswith("/"):
                    candidate = root.joinpath(*PurePosixPath(relative_target[1:]).parts)
                else:
                    candidate = root.joinpath(
                        *source_relative.parent.parts,
                        *PurePosixPath(relative_target).parts,
                    )

                try:
                    resolved = candidate.resolve(strict=False)
                    resolved.relative_to(root)
                except (OSError, RuntimeError, ValueError):
                    errors.append(
                        f"{document}:{line_number}: local link escapes the repository: {target}"
                    )
                    continue

                resolved_relative = PurePosixPath(resolved.relative_to(root).as_posix())
                if _is_protected_outcome_path(resolved_relative):
                    errors.append(
                        f"{document}:{line_number}: protected Test/Leaderboard outcome "
                        f"link is not checked: {target}"
                    )
                    continue

                if resolved.exists():
                    continue

                title = (match.group("title") or "").strip("\"'()")
                if (
                    (resolved_relative.parts[:1] == ("artifacts",)
                     or resolved_relative.parts[:1] == ("outputs",))
                    and _DOCUMENTED_EXTERNAL_EVIDENCE_TITLE.search(title)
                ):
                    continue

                errors.append(
                    f"{document}:{line_number}: local link target does not exist: {target}"
                )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check local links in current-facing AtlasOps review Markdown."
    )
    parser.add_argument(
        "documents",
        nargs="*",
        help="Optional repository-relative Markdown paths (defaults to the current review set).",
    )
    args = parser.parse_args()

    errors = check_current_markdown_links(Path.cwd(), args.documents or CURRENT_MARKDOWN_PATHS)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    print(f"Local links resolved in {len(args.documents or CURRENT_MARKDOWN_PATHS)} review documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
