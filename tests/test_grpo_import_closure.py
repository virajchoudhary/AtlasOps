"""Controlled P2 imports cannot require the optional P1 HTTP serving stack."""

import subprocess
import sys


def test_observed_controlled_state_without_optional_uvicorn():
    code = """
import sys,importlib.abc
class DenyUvicorn(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='uvicorn' or fullname.startswith('uvicorn.'):
   raise ModuleNotFoundError('optional uvicorn must not import on controlled P2 path')
sys.meta_path.insert(0,DenyUvicorn())
from training.grpo_observation_first import _public_snapshot
from training.grpo_controlled import CapacityEnvironment,SCENARIO
from scripts import reload_grpo_controlled,accept_grpo_controlled
class Ledger:
 def append(self,*args):pass
env=CapacityEnvironment(Ledger())
snapshot=env.begin(SCENARIO)
raw,digest=_public_snapshot(snapshot['state'])
assert raw and digest
assert 'uvicorn' not in sys.modules
print('CONTROLLED_IMPORT_CLOSURE_PASS')
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_actual_http_server_refuses_when_optional_dependency_missing():
    code = """
import sys,importlib.abc,asyncio
class DenyUvicorn(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='uvicorn':raise ModuleNotFoundError('uvicorn unavailable')
sys.meta_path.insert(0,DenyUvicorn())
from agents.approval import ApprovalGate
from agents.approval_http import loopback_approval_server
async def check():
 try:
  async with loopback_approval_server(ApprovalGate(),'fixture-key'):
   raise AssertionError('server yielded without dependency')
 except ModuleNotFoundError:pass
asyncio.run(check())
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
