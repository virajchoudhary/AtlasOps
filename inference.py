"""Retired synthetic-alert runner; use governed inference and incident entrypoints."""


def main() -> None:
    raise SystemExit(
        "Legacy synthetic-alert inference is disabled. Use the governed "
        "Stage 6/8/9 evaluators for model evaluation, the Stage 4 harness "
        "for explicitly authorized live incidents, or dashboard.py for "
        "the read-only evidence demo."
    )


if __name__ == "__main__":
    main()
