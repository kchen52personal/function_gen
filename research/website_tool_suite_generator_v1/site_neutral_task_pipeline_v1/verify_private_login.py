"""Restore private state into one isolated, fresh browser; never invoke a model."""
import argparse
import json
import os
from pathlib import Path
import shutil
from public_environment import RUNTIME, IsolatedPublicEnvironment, workspace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('session_directory')
    parser.add_argument('--selector', required=True)
    parser.add_argument('--session-cookie-name', required=True)
    parser.add_argument('--resource-host', action='append', default=[])
    args = parser.parse_args()
    os.umask(0o077)
    parent = Path('/shared/hans/.secrets/browser_sessions').resolve(strict=True)
    session = Path(args.session_directory).resolve(strict=True)
    if session.parent != parent or not session.is_dir():
        raise ValueError('expected an existing private browser session directory')
    state = session/'storage_state.json'
    if state.is_symlink() or not state.is_file() or state.stat().st_mode & 0o077:
        raise ValueError('private session file missing or overly broad permissions')
    metadata = json.loads((session/'metadata.json').read_text())
    if not metadata.get('login_cookie_received'):
        raise ValueError('no login session marker was received')
    work = workspace()
    work.chmod(0o700)
    shutil.copyfile(state, work/'private_storage_state.json')
    (work/'private_storage_state.json').chmod(0o600)
    shutil.copyfile(RUNTIME/'public_session_probe.py', work/'probe.py')
    environment = IsolatedPublicEnvironment(session, metadata['website'], resource_hosts=args.resource_host)
    result = environment.run_command(work, ['python3', str(work/'probe.py'), metadata['website'],
        args.selector, args.session_cookie_name], session/'reuse_network.jsonl', 180, login_bootstrap=True)
    (session/'reuse_stdout.log').write_text(result.stdout)
    (session/'reuse_stderr.log').write_text(result.stderr)
    record = {'status':'probe_failed', 'process_exit':result.returncode, 'model_calls':0, 'gpu_used':False}
    if result.returncode==0 and (work/'probe_result.json').is_file():
        record.update(json.loads((work/'probe_result.json').read_text()))
    (session/'reuse_result.json').write_text(json.dumps(record, indent=2)+'\n')
    print(json.dumps(record), flush=True)


if __name__=='__main__':
    main()
