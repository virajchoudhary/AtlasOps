"""Retired synthetic-alert runner that dispatched real remediation."""


def main() -> None:
    raise SystemExit(
        "Legacy quick evaluation is disabled. For local NON_EMPIRICAL fixtures, "
        "use python -m bench.runner --model fixture --mock --adversarial 0. "
        "Real evaluation requires the governed Stage 6/8/9 contracts."
    )


if __name__ == "__main__":
    main()
