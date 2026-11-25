"""Bounded user-approved QR login; excludes cookies from ordinary run artifacts.

This does not start development/evaluation or give the session to any model.
Session replay into subsequent commands requires a separate integration step.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4
from public_environment import PROJECT, RUNTIME, IsolatedPublicEnvironment, workspace
from runtime.public_login_bootstrap import session_cookie_present
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('website')
    parser.add_argument('--selector', required=True)
    parser.add_argument('--session-cookie-name', required=True)
    parser.add_argument('--resource-host', action='append', default=[])
    args = parser.parse_args()
    os.umask(0o077)
    private = Path('/shared/hans/.secrets/browser_sessions')
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    private.chmod(0o700)
    session = private/('session_'+uuid4().hex[:12])
    session.mkdir(mode=0o700)
    work = workspace()
    work.chmod(0o700)
    shutil.copyfile(RUNTIME/'public_login_bootstrap.py', work/'login_bootstrap.py')
    metadata = {'website':args.website, 'workspace':str(work), 'session_directory':str(session),
                'started_at':datetime.now(timezone.utc).isoformat(),
                'purpose':'user-assisted login only, excluded from development/synthesis',
                'model_calls':0, 'gpu_used':False, 'session_replay_implemented':False}
    (session/'metadata.json').write_text(json.dumps(metadata, indent=2)+'\n')
    print(json.dumps({'private_workspace':str(work), 'status':str(work/'status.json'),
                      'qr_image':str(work/'login.png'), 'session_directory':str(session)}), flush=True)
    environment = IsolatedPublicEnvironment(session, args.website, resource_hosts=args.resource_host)
    try:
        result = environment.run_command(work, ['python3', str(work/'login_bootstrap.py'),
            args.website, args.selector, args.session_cookie_name], session/'network.jsonl', 780,
            login_bootstrap=True)
        # Tracebacks may contain URLs: keep them private, never print cookie values.
        (session/'stdout.log').write_text(result.stdout)
        (session/'stderr.log').write_text(result.stderr)
        state = work/'private_storage_state.json'
        if state.is_file() and not state.is_symlink():
            target = session/'storage_state.json'
            shutil.copyfile(state, target)
            target.chmod(0o600)
        metadata.update(process_exit=result.returncode,
                        session_state_saved=(session/'storage_state.json').is_file(),
                        login_cookie_received=state.is_file() and session_cookie_present(
                            json.loads(state.read_text()).get('cookies', []),
                            args.session_cookie_name, urlparse(args.website).hostname))
    finally:
        metadata['finished_at'] = datetime.now(timezone.utc).isoformat()
        (session/'metadata.json').write_text(json.dumps(metadata, indent=2)+'\n')
    print(json.dumps({'process_exit':result.returncode,
                      'session_state_saved':metadata['session_state_saved'],
                      'login_cookie_received':metadata['login_cookie_received']}), flush=True)


if __name__ == '__main__':
    main()
