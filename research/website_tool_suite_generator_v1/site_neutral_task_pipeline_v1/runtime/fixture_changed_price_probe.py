"""Replay saved scripts on a disposable copy of the local store with one changed price."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from uuid import uuid4

ROOT = Path('/shared/hans/research/website_tool_suite_generator_v1')
RUN = ROOT / 'automated_runs/fixture_changed_price_20261117_v1'
SOURCE = ROOT / 'automated_runs/container_isolation_20261117_v1/fixture'
IMAGE = 'sha256:a40b28c1872002f1761da1fde2f890ad509a90939136935a8ef9736e8fa17f19'
SHELL = ROOT / 'site_neutral_task_pipeline_v1/runtime/docker_fixture_shell.py'
CASES = {
    'lookup': ROOT / 'ct_webwright_20261117v2/final_script.py',
    'sort': ROOT / 'ct_fixture_sort_20261117v2/final_script.py',
}


def main():
    RUN.mkdir(parents=True, exist_ok=False)
    fixture = RUN / 'fixture'
    shutil.copytree(SOURCE, fixture)
    asset = fixture / 'dist/assets/index-7Zc1JihY.js'
    text = asset.read_text()
    old = 'title:"Aluminum Laptop Stand",price:59,sku:"JKY-ST-AL"'
    new = 'title:"Aluminum Laptop Stand",price:65,sku:"JKY-ST-AL"'
    if text.count(old) != 1:
        raise ValueError('expected unique price record not found')
    asset.write_text(text.replace(old, new, 1))
    nonce = uuid4().hex
    name = 'ww-fixture-probe-' + nonce[:12]
    state = {'status': 'starting', 'old_price': 59, 'changed_price': 65, 'cases': {},
             'purpose': 'controlled test of generated scripts, not a benchmark'}
    site_id = None
    started = time.monotonic()
    try:
        cmd = ['/usr/bin/docker','run','--detach','--rm','--pull=never','--name',name,
            '--label',f'hans.fixture.nonce={nonce}','--network','none','--cap-drop','ALL',
            '--security-opt','no-new-privileges','--pids-limit','64','--cpus','1','--memory','256m',
            '--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=64m','--user',f'{os.getuid()}:{os.getgid()}',
            '--mount',f'type=bind,src={fixture},dst=/fixture,readonly','--entrypoint','/usr/bin/timeout',
            IMAGE,'500s','python3','/fixture/serve_fixture.py','--port','18765']
        site_id = subprocess.run(cmd,capture_output=True,text=True,timeout=20,check=True).stdout.strip()
        if not re.fullmatch(r'[0-9a-f]{64}',site_id): raise ValueError('invalid container ID')
        for case, script in CASES.items():
            workspace = RUN / case
            workspace.mkdir()
            (workspace / '.container_bin').mkdir()
            (workspace / '.container_bin/python').symlink_to('/usr/bin/python3')
            shutil.copy2(script, workspace / 'final_script.py')
            env = {**os.environ, 'WORKSPACE_DIR':str(workspace), 'FIXTURE_CONTAINER_ID':site_id,
                   'FIXTURE_SESSION_NONCE':nonce}
            result = subprocess.run([str(SHELL),'-c','python3 "$WORKSPACE_DIR/final_script.py"'],
                                    env=env,capture_output=True,text=True,timeout=140,check=False)
            (workspace / 'stdout.log').write_text(result.stdout)
            (workspace / 'stderr.log').write_text(result.stderr)
            logs = list((workspace / 'final_runs').glob('run_*/final_script_log.txt'))
            content = '\n'.join(x.read_text() for x in logs)
            state['cases'][case] = {'process_exit': result.returncode,
                'log_contains_changed_price': '$65.00' in content,
                'log_contains_old_price': '$59.00' in content,
                'log_paths': [str(p) for p in logs]}
        state['status'] = 'finished'
    except Exception as exc:
        state.update(status='error', error_type=type(exc).__name__, error=str(exc)[:500])
    finally:
        if site_id:
            result = subprocess.run(['/usr/bin/docker','stop','--time','3',site_id],capture_output=True,
                                    text=True,timeout=12,check=False)
            state['site_stop_returncode'] = result.returncode
        state['elapsed_seconds'] = round(time.monotonic()-started,2)
        (RUN / 'summary.json').write_text(json.dumps(state,indent=2)+'\n')


if __name__ == '__main__':
    main()
