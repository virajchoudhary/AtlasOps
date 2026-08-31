# AtlasOps — Cloud GPU Training Notebooks (Kaggle & Colab)

This directory contains standalone Jupyter notebooks preconfigured for running heavy GPU model training and RL optimization on free cloud accelerators (**Kaggle 2x T4 GPUs / 30 hours per week** or **Google Colab Free T4**).

---

## Available Notebooks

| Notebook | Purpose | Recommended Hardware | Description |
| :--- | :--- | :--- | :--- |
| [`kaggle_sft_training.ipynb`](kaggle_sft_training.ipynb) | **Supervised Fine-Tuning (Stage 7 & 8)** | Kaggle GPU T4 x2 or P100 | Runs 4-bit NF4 QLoRA on `Qwen/Qwen2.5-7B-Instruct` across the 64 multi-agent trajectory examples with Qwen template loss masking. |
| [`kaggle_grpo_training.ipynb`](kaggle_grpo_training.ipynb) | **Online GRPO RL (Stage 9)** | Kaggle GPU T4 x2 or P100 | Executes Group Relative Policy Optimization with group advantage normalization ($A_i = \frac{r_i - \mu}{\sigma + \epsilon}$) and objective contract evaluation. |

---

## How to Run on Kaggle (Step-by-Step)

1. Go to [kaggle.com](https://www.kaggle.com/) and click **"+ Create"** $\rightarrow$ **"New Notebook"**.
2. In the top-right notebook menu, click **"File"** $\rightarrow$ **"Import Notebook"** and upload either `kaggle_sft_training.ipynb` or `kaggle_grpo_training.ipynb`.
3. In the right-hand panel (**Notebook Settings**):
   - Set **Accelerator** to **GPU T4 x2** (or **GPU P100**).
   - Set **Internet** to **On** (required to clone GitHub and download Hugging Face model weights).
4. Click **"Run All"** (or run cells sequentially).
5. Output adapter weights are saved to `/kaggle/working/` and can be downloaded or pushed to Hugging Face Hub directly.

---

## How to Run on Google Colab

1. Go to [colab.research.google.com](https://colab.research.google.com/).
2. Click **"File"** $\rightarrow$ **"Upload notebook"** and upload the notebook.
3. In the top menu, select **Runtime** $\rightarrow$ **Change runtime type** $\rightarrow$ **T4 GPU**.
4. Run all cells.
