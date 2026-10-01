"""Retired legacy live evaluator; retained only to explain its replacement."""


def main() -> None:
    raise SystemExit(
        "Legacy live evaluation is disabled. Use bench.zero_shot_baseline, "
        "bench.sft_eval, or bench.grpo_eval under their governed contracts; "
        "live incidents require the Stage 4 harness and explicit authorization."
    )


if __name__ == "__main__":
    main()
