"""Retired synthetic-alert SFT generator.

This path used unrestricted scenario IDs and model-claimed outcome rewards.
Current data preparation uses the frozen Train split in
``training.build_sft_dataset``.
"""


def main() -> None:
    raise SystemExit(
        "Legacy synthetic-alert trajectory generation is disabled; "
        "use training.build_sft_dataset"
    )


if __name__ == "__main__":
    main()
