"""Pinned-library reload under the guard with random local CPU artifacts only."""

import os
import subprocess
import sys

import pytest


def test_fresh_local_peft_reload_under_permanent_guard(tmp_path):
    code = r"""
from pathlib import Path
import sys
from transformers import GPT2Config, GPT2LMHeadModel
from peft import LoraConfig, get_peft_model
p=Path(sys.argv[1])
base=GPT2LMHeadModel(GPT2Config(vocab_size=8,n_embd=16,n_layer=1,n_head=2))
base.save_pretrained(p/'base')
get_peft_model(base,LoraConfig(r=2,target_modules=['c_attn'],task_type='CAUSAL_LM')).save_pretrained(p/'adapter')
"""
    try:
        __import__("peft")
    except ImportError:
        if os.environ.get("ATLASOPS_REQUIRE_TRL_INTEGRATION") == "1":
            pytest.fail("Pinned reload stack missing")
        pytest.skip("Optional pinned reload stack unavailable")
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    guarded = r"""
from pathlib import Path
import sys
from training.grpo_reload_isolation import LoopbackGuard
g=LoopbackGuard();g.install()
from transformers import AutoModelForCausalLM
from peft import PeftModel
g.require_clean();g.install_syscall_guard()
p=Path(sys.argv[1])
b=AutoModelForCausalLM.from_pretrained(p/'base',local_files_only=True)
m=PeftModel.from_pretrained(b,p/'adapter',local_files_only=True,is_trainable=False)
print('GUARD_ATTEMPTS',g.attempts)
g.require_clean()
assert any('lora_' in n for n,_ in m.named_parameters())
print('LOCAL_GUARDED_RELOAD_PASS')
"""
    result = subprocess.run([sys.executable, "-c", guarded, str(tmp_path)],
                            capture_output=True, text=True, timeout=30,
                            env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    assert result.returncode == 0, result.stderr
    assert "LOCAL_GUARDED_RELOAD_PASS" in result.stdout
