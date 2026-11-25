"""Host-selected container execution adapter for the existing adaptive pipeline.

No task selectors or expected answers: this adapter changes the runtime and
local URL policy; the task factory and synthesis remain model-driven.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from uuid import uuid4

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parent
sys.path[:0]=[str(HERE/'runtime'),str(PROJECT/'pipeline_codex_v73')]
from codex_bounded_process import run_bounded
from runtime.fixture_session import FixtureSession
import factory
from factory import cli_value
import synthesis

URL='http://127.0.0.1:18765/'


class IsolatedFixtureEnvironment:
    name='isolated_local_fixture_submission_only'

    def __init__(self,root,*,allow_cart=False):
        self.root=root
        self.allow_cart=allow_cart
        self.fixture_version='cart_v2' if allow_cart else 'legacy'
        self.session=None
        self.counter=0

    def __enter__(self):
        self.session=FixtureSession(self.fixture_version).__enter__()
        return self

    def __exit__(self,*exc):
        return self.session.__exit__(*exc)

    def validate_site(self,url,idea):
        if url!=URL or not isinstance(idea,str) or not 5<=len(idea.strip())<=1000:
            raise ValueError('fixed isolated fixture URL and bounded idea required')

    async def reconnaissance(self,url,output,max_pages=3):
        self.validate_site(url,'Read only reconnaissance')
        output.mkdir(parents=True,exist_ok=False)
        shutil.copyfile(HERE/'runtime/fixture_recon_probe.py',output/'probe.py')
        result=await asyncio.to_thread(self.session.run,output,['python3',str(output/'probe.py')])
        (output/'stdout.log').write_text(result.stdout);(output/'stderr.log').write_text(result.stderr)
        if result.returncode: raise RuntimeError('fixture reconnaissance failed')
        return json.loads((output/'recon.json').read_text())

    def create_taskset(self,url,idea,recon,output,**kwargs):
        return factory.create_taskset(url,idea,recon,output,allow_test_fixture=True,allow_fixture_cart=self.allow_cart,**kwargs)

    def synthesize(self,url,contract,examples,output):
        return synthesis.synthesize(url,contract,examples,output,allow_test_fixture=True,allow_fixture_cart=self.allow_cart)

    def propose_replacements(self,url,contract,prior,registry,count,round_index,output):
        return factory.propose_replacements(url,contract,prior,registry,count,round_index,output,allow_fixture_cart=self.allow_cart)

    def run_development(self,url,contract,tasks,output,*,attempt_timeout,queue_timeout):
        self.validate_site(url,'Read only development')
        output.mkdir(parents=True,exist_ok=False)
        state={'status':'running','attempts':[],'selection':'formal WebWright submission only; visual verifier has no vote'}
        def save():
            tmp=output/'.summary.tmp';tmp.write_text(json.dumps(state,indent=2)+'\n');tmp.replace(output/'summary.json')
        started=time.monotonic();save()
        for task in tasks:
            remaining=queue_timeout-(time.monotonic()-started)
            if remaining<60: break
            self.counter+=1
            token=uuid4().hex[:10]
            workspace=PROJECT/f'fe2e_{token}'
            run_name=f'fixture_e2e_agent_20261118_{token}'
            task_text=(task['intent']+'\nExact development parameters: '+json.dumps(task['parameters'])+
                '\nFunction contract: '+json.dumps(contract)+
                '\nSave and run final_script.py, then complete the built-in self-check and submit. '
                'Produce final_script_log.txt in a final_runs/run_ID directory. Its last line must begin '
                'final result: followed by JSON with exactly parameters, result, and source_url. '
                'Echo the exact input parameters. Use the declared result fields with the declared types. '
                'source_url must be the isolated local fixture URL http://127.0.0.1:18765/. '
                'Save no more than two final screenshots. Read all returned data from the browser. '+
                ('Cart changes and promo application are allowed only on the isolated demo. Never check out, purchase, or place an order. ' if self.allow_cart else 'Do not modify cart or checkout. ')+
                'Do not invent data or read host source files.')
            spec={'task':task_text,'success_criteria':[task['intent'],contract['success_contract'],
                  'Correct declared output fields and typed input parameters; complete self-check and submit.'],
                  'requested_constraints':['Local demo cart changes only; never buy, place orders, or check out.' if self.allow_cart else 'Read-only local demonstration; do not change cart, buy, or check out.']}
            spec_path=output/f"{task['task_id']}_spec.json"
            spec_path.write_text(json.dumps(spec,indent=2)+'\n')
            cmd=[sys.executable,str(HERE/'runtime/container_fixture_webwright_smoke.py'),
                 '--run-name',run_name,'--workspace-name',workspace.name,'--agent-calls','40',
                 '--inner-calls','6','--command-seconds','900','--fixture-version',self.fixture_version,
                 '--wall-seconds',str(max(60,min(1800,int(attempt_timeout),int(remaining)-15))),
                 '--task-file',str(spec_path)]
            if self.allow_cart:cmd.append('--allow-fixture-cart')
            code,stdout,stderr=run_bounded(cmd,cwd=PROJECT,env=os.environ.copy(),timeout=min(remaining,attempt_timeout+30))
            attempt=output/task['task_id']/'qwen3.5-plus';attempt.mkdir(parents=True)
            (attempt/'stdout.log').write_text(stdout);(attempt/'stderr.log').write_text(stderr)
            path=PROJECT/'automated_runs'/run_name/'summary.json'
            info=json.loads(path.read_text()) if path.exists() else {}
            if workspace.exists():
                # Preserve plain artifact files without carrying runtime sockets or symlinks.
                dest=attempt/'agent';dest.mkdir()
                for candidate in workspace.rglob('*'):
                    if candidate.is_symlink() or not candidate.is_file(): continue
                    target=dest/candidate.relative_to(workspace);target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(candidate,target)
            script=attempt/'agent/final_script.py'
            row={'task_id':task['task_id'],'process_exit':info.get('process_exit',code),
                 'trajectory_status':info.get('trajectory_status'),
                 'original_agent_run':run_name,'original_workspace':str(workspace)}
            if script.is_file(): row['script_sha256']=hashlib.sha256(script.read_bytes()).hexdigest()
            state['attempts'].append(row);save()
        state.update(status='attempts_complete',elapsed_seconds=round(time.monotonic()-started,2));save()
        return output

    def run_ungraded_case(self,task,script,output):
        output.mkdir(parents=True,exist_ok=False)
        workspace=output/'workspace';workspace.mkdir()
        copy=workspace/'candidate.py';shutil.copyfile(script,copy)
        flags=[part for k,v in task['parameters'].items() for part in (f'--{k}',cli_value(v))]
        result=self.session.run(workspace,['python3',str(copy),*flags,'--output-dir',str(workspace/'execution')])
        (output/'stdout.log').write_text(result.stdout);(output/'stderr.log').write_text(result.stderr)
        state={'task_id':task['task_id'],'status':'ran_ungraded' if result.returncode==0 else 'execution_failed',
               'process_exit':result.returncode,'semantic_verdict':None,
               'reason':'Frozen function executed; visual-verifier results are separate shadow diagnostics.'}
        (output/'summary.json').write_text(json.dumps(state,indent=2)+'\n')
        return state

    def run_development_verification(self,task,script,output):
        """Replay once on the disposable local fixture; never use a real account."""
        return self.run_ungraded_case(task,script,output)
