"""One local-browser capture and offline signature/tamper check.

Runs a host-authored fixture QA probe, NOT WebWright or a model-generated tool.
No external sites, private accounts, VLM APIs, GPU or held tasks are used.
"""
import json
from pathlib import Path
import secrets
import shutil
from uuid import uuid4
import evidence_capsule as cap
from fixture_environment import IsolatedFixtureEnvironment

ROOT=Path('/shared/hans/research/website_tool_suite_generator_v1')
PROBE=Path(__file__).parent/'runtime/fixture_single_action_capture.py'


def main():
    output=ROOT/'automated_runs'/('verifier_fixture_capsule_demo_20261124_'+uuid4().hex[:8])
    output.mkdir(mode=0o700)
    workspace=output/'trusted_fixture_workspace';workspace.mkdir(mode=0o700)
    shutil.copyfile(PROBE,workspace/'probe.py')
    state={'status':'running','fixture_only':True,'candidate_executed':False,
           'replayed':False,'eligible_for_pipeline_gate':False,'held_tasks_opened':False,
           'limitations':['Fixture QA only; no generated candidate or independent observer.',
              'Probe-generated screenshots live in the test workspace before signing.',
              'Test key is kept only in this process and is not a production key.']}
    (output/'summary.json').write_text(json.dumps(state,indent=2)+'\n')
    try:
        with IsolatedFixtureEnvironment(output,allow_cart=True) as env:
            result=env.session.run(workspace,['python3',str(workspace/'probe.py')])
            (output/'probe_stdout.log').write_text(result.stdout)
            (output/'probe_stderr.log').write_text(result.stderr)
            state['process_exit']=result.returncode
            if result.returncode: raise RuntimeError('fixture probe failed')
        report=json.loads((workspace/'capture_report.json').read_text())
        state['probe_report']=report
        if report.get('scripted_clicks')!=1 or report.get('status')!='captured':
            raise RuntimeError('fixture capture incomplete')
        key=secrets.token_bytes(32)
        task={'task_id':'fixture_one_action','intent':'Click one local demo product add button once.'}
        script=(workspace/'probe.py').read_bytes()
        captures=[{'role':'before','data':(workspace/'before.png').read_bytes()},
                  {'role':'after','data':(workspace/'after.png').read_bytes()}]
        capsule=output/'signed_capsule'
        cap.create_capsule(capsule,run_id='fixture_one_action',task=task,script=script,captures=captures,key=key)
        state['original_integrity']=cap.verify_capsule(capsule,run_id='fixture_one_action',task=task,script=script,key=key)
        tampered=output/'tampered_copy';shutil.copytree(capsule,tampered)
        (tampered/'capture_01.png').write_bytes(captures[0]['data'])
        state['tampered_integrity']=cap.verify_capsule(tampered,run_id='fixture_one_action',task=task,script=script,key=key)
        state['status']='complete'
    except Exception as exc:
        state.update(status='failed',error_type=type(exc).__name__)
    (output/'summary.json').write_text(json.dumps(state,indent=2)+'\n')
    print(json.dumps({'output':str(output),'status':state['status'],'process_exit':state.get('process_exit'),
                      'error_type':state.get('error_type')}),flush=True)


if __name__=='__main__':main()
