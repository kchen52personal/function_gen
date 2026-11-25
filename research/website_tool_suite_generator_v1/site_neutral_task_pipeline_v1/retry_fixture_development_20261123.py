"""Retry one previously failed development task on the disposable fixture only."""
import json
from pathlib import Path
from fixture_environment import IsolatedFixtureEnvironment, PROJECT, URL

SOURCE=PROJECT/'automated_runs/verifier_2dev1held_20261123_v1'
OUT=PROJECT/'automated_runs/verifier_fixture_dev_retry_20261123_v1'


def main():
    if OUT.exists():
        raise FileExistsError('refusing to overwrite an existing run')
    state=json.loads((SOURCE/'summary.json').read_text())
    development=json.loads((SOURCE/'tasks/development/tasks.json').read_text())['tasks']
    task=next(x for x in development if x['task_id']=='task_002')
    OUT.mkdir(mode=0o700)
    (OUT/'request.json').write_text(json.dumps({'site':URL,'task':task,'contract':state['contract'],
        'purpose':'retry only; no held input, no synthesis, no real account'},indent=2)+'\n')
    with IsolatedFixtureEnvironment(OUT) as env:
        env.run_development(URL,state['contract'],[task],OUT/'development',
                            attempt_timeout=360,queue_timeout=390)
    print((OUT/'development/summary.json').read_text(),flush=True)

if __name__=='__main__': main()
