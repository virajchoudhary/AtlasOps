# AtlasOps - Retired Cloud Training Shortcuts

The two historical notebooks are disabled compatibility artifacts. They are not
current Kaggle or Colab training procedures, evidence of GPU availability, or
authorization to download weights, train, or read final-Test results. Their
original cells remain in Git history; neither notebook stored training outputs.

## Retired Paths

| Notebook | Reason |
| :--- | :--- |
| [kaggle_sft_training.ipynb](kaggle_sft_training.ipynb) | Unpinned dependencies and an incomplete SFT invocation without required revision, admitted corpus, and host-plan provenance. |
| [kaggle_grpo_training.ipynb](kaggle_grpo_training.ipynb) | Mock evaluators labeled as online GRPO; no actual training/checkpoint execution or held-out admission. |

Both code cells fail immediately without imports, shell commands, installation,
filesystem writes, or accelerator requests.

## Current References

- [Stage 7 data and training contract](../docs/project/STAGE_7_SFT_DATA_AND_TRAINING.md)
- [SFT pilot acceptance](../docs/project/G7_SFT_PILOT_ACCEPTANCE_V1.md)
- [Named-host execution plan](../docs/project/G7_NAMED_HOST_EXECUTION_PLAN_V1.md)
- [Stage 9 online GRPO contract](../docs/project/STAGE_9_ONLINE_GRPO.md)
- [Observation-first software candidate](../docs/project/G9_OBSERVATION_FIRST_SOFTWARE_CANDIDATE_V1.md)
- [Remote training readiness](../docs/project/G7_G9_REMOTE_TRAINING_READINESS.md)

The project remains `NOT_CERTIFIED`. Preparation and software tests are distinct
from approved model transfer, successful training, and empirical evaluation.
