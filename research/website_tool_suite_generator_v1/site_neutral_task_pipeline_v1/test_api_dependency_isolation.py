"""The API-only verifier must not import local GPU model libraries."""
import os
import subprocess
import sys
from pathlib import Path


def test_api_pipeline_imports_without_torch_or_transformers():
    here=Path(__file__).parent
    program='''
import builtins
real_import=builtins.__import__
def guarded(name,*args,**kwargs):
    if name.split('.')[0] in {'torch','transformers'}:
        raise AssertionError('unexpected GPU-model import: '+name)
    return real_import(name,*args,**kwargs)
builtins.__import__=guarded
import visual_verifier, coverage_verifier, adaptive
'''
    env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1'
    completed=subprocess.run([sys.executable,'-c',program],cwd=here,env=env,
                             capture_output=True,text=True,timeout=30)
    assert completed.returncode==0, completed.stderr[-3000:]


def test_gpu_refusal_happens_before_model_import():
    here=Path(__file__).parent
    program='''
import builtins,sys
sys.path.insert(0,'../pipeline_codex_v73')
real_import=builtins.__import__
def guarded(name,*args,**kwargs):
    if name.split('.')[0] in {'torch','transformers'}:
        raise AssertionError('unexpected GPU-model import: '+name)
    return real_import(name,*args,**kwargs)
builtins.__import__=guarded
from local_propose import require_scoped_gpu_approval
try:
    require_scoped_gpu_approval(0,8)
except RuntimeError as e:
    assert 'GPU launch refused' in str(e)
else:
    raise AssertionError('unapproved GPU launch was not refused')
'''
    env=os.environ.copy();env.pop('WEBSITE_TOOL_GPU_APPROVED',None);env['PYTHONDONTWRITEBYTECODE']='1'
    completed=subprocess.run([sys.executable,'-c',program],cwd=here,env=env,
                             capture_output=True,text=True,timeout=30)
    assert completed.returncode==0, completed.stderr[-3000:]
