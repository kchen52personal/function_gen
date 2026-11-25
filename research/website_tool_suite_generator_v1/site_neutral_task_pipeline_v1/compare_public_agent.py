"""Development-only same-sandbox agent comparison; never reads held inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from public_environment import IsolatedPublicEnvironment


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    args.source=args.source.resolve(strict=True)
    args.output=args.output.resolve()
    summary=json.loads((args.source/'summary.json').read_text())
    manifest=args.source/'tasks/development/tasks.json'
    digest=hashlib.sha256(manifest.read_bytes()).hexdigest()
    if digest!=summary['split_hashes']['development']:
        raise ValueError('development manifest hash mismatch')
    tasks=json.loads(manifest.read_text())['tasks']
    by_id={t['task_id']:t for t in tasks}
    selected=[by_id[x] for x in ('task_001','task_004')]
    contract=json.loads((args.source/'tasks/contract.json').read_text())
    args.output.mkdir(parents=True,exist_ok=False)
    state={'status':'running','source':str(args.source),'manifest_sha256':digest,
        'tasks':[t['task_id'] for t in selected], 'outer_model':'qwen3.8-flash',
        'inner_model':'qwen3.5-plus','held_opened':False,
        'selection':'retrospective convenience sample: earlier 3.5 API timeout and submission',
        'limitations':['Two cases are not a model ranking; selected after seeing 3.5 outcomes.',
                       'Live website and backend load may differ.',
                       'Submission is not independently verified correctness.']}
    (args.output/'summary.json').write_text(json.dumps(state,indent=2)+'\n')
    env=IsolatedPublicEnvironment(args.output,summary['website'],agent_steps=60,agent_calls=80,
        inner_calls=12,diagnostic_public_get=True,agent_model='qwen3.8-flash')
    try:
        out=env.run_development(summary['website'],contract,selected,args.output/'development',
            attempt_timeout=1800,queue_timeout=3680)
        state.update(status='completed',development_summary=str(out/'summary.json'))
    except Exception as exc:
        state.update(status='stopped_error',error_type=type(exc).__name__,error=str(exc)[:300])
        raise
    finally:
        env.close()
        state['finished_unix']=time.time()
        (args.output/'summary.json').write_text(json.dumps(state,indent=2)+'\n')


if __name__=='__main__':
    main()
