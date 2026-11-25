"""Negative test: can the confined agent reach a host Unix-domain socket?"""
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading

ROOT = Path('/shared/hans/research/website_tool_suite_generator_v1')
FIXTURE = ROOT/'local_fixtures/ecommerce_storefront_20261117'
OUTPUT = FIXTURE/'fixture_host_unix_probe_20261117_v1.json'
WRAPPER = Path(__file__).with_name('sandbox_fixture_v1.py')
CHILD = '''
import json, socket, sys
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(2); sock.connect(sys.argv[1]); sock.sendall(b'probe')
    print(json.dumps({'host_unix':'connected'}))
except OSError as exc:
    print(json.dumps({'host_unix':type(exc).__name__}))
'''


def main():
    with tempfile.TemporaryDirectory(prefix='.host_unix_', dir='/shared/hans') as parent, \
         tempfile.TemporaryDirectory(prefix='.fixture_agent_', dir=ROOT) as agent:
        outside = Path(parent)/'host.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(outside))
            listener.listen(1)
            listener.settimeout(6)
            accepted = []
            def listen():
                try:
                    with listener.accept()[0] as connection:
                        accepted.append(connection.recv(16).decode())
                except OSError:
                    pass
            thread = threading.Thread(target=listen, daemon=True)
            thread.start()
            run = subprocess.run(['/shared/miniconda3/bin/python', str(WRAPPER),
                '/shared/miniconda3/bin/python', '-c', CHILD, str(outside)],
                env={'WORKSPACE_DIR':agent, 'FIXTURE_ROOT':str(FIXTURE)},
                capture_output=True, text=True, timeout=30, check=False)
            thread.join(timeout=7)
            try:
                result = json.loads(run.stdout)
            except ValueError:
                result = {'parse_error':True}
            result.update(returncode=run.returncode, stderr=run.stderr[:1000],
                          host_accepted=bool(accepted))
            result['passed'] = (run.returncode == 0 and result.get('host_unix') != 'connected'
                                and not accepted)
    with OUTPUT.open('x') as handle:
        json.dump(result,handle,indent=2); handle.write('\n')
    print(json.dumps(result,indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
