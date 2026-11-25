"""No-model public transport/browser checks before any experiment launch."""
import json
import argparse
from pathlib import Path
import shutil
import socket
from uuid import uuid4
from public_environment import IsolatedPublicEnvironment, PROJECT, RUNTIME, snapshot, workspace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--website', default='https://arxiv.org/')
    parser.add_argument('--resource-host',action='append',default=[])
    args = parser.parse_args()
    root = PROJECT/'automated_runs'/('public_site_preflight_20261118_'+uuid4().hex[:8])
    root.mkdir()
    work = workspace()
    for name in ('public_security_probe.py', 'public_recon_probe.py'):
        shutil.copyfile(RUNTIME/name, work/name)
    unix_path = PROJECT/('.probe_'+uuid4().hex[:12]+'.sock')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as host_unix, socket.socket() as host_tcp:
        host_unix.bind(str(unix_path))
        host_unix.listen()
        host_tcp.bind(('127.0.0.1', 0))
        host_tcp.listen()
        env = IsolatedPublicEnvironment(root, args.website,resource_hosts=args.resource_host)
        try:
            result = env.run_command(work, ['python3', str(work/'public_security_probe.py'),
                env.url, str(unix_path), str(host_tcp.getsockname()[1])], root/'network.jsonl', 180)
        finally:
            unix_path.unlink()  # Only this probe's randomly named ephemeral socket.
    (root/'stdout.log').write_text(result.stdout)
    (root/'stderr.log').write_text(result.stderr)
    snapshot(work, root/'browser_artifacts')
    state = {'status': 'passed' if result.returncode == 0 else 'failed', 'process_exit': result.returncode,
             'root': str(root), 'workspace': str(work), 'model_calls': 0, 'gpu_used': False,
             'website': args.website,
             'resource_hosts':args.resource_host,
             'scope': 'Targeted negative controls and actual browser reachability; not a full security audit.'}
    (root/'summary.json').write_text(json.dumps(state, indent=2)+'\n')
    print(json.dumps(state), flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
