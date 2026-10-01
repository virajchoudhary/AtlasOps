"""Retired unpinned model-export shortcut.

The historical exporter loaded remote model code without immutable revisions
and could publish merged weights without checkpoint provenance admission.
Its original implementation remains in Git history. Export and publication
require a separately reviewed, explicitly authorized workflow.
"""


def main() -> None:
    raise SystemExit(
        "Legacy model export is disabled; use a separately reviewed, "
        "checkpoint-provenance-bound and explicitly authorized export plan"
    )


if __name__ == "__main__":
    main()
