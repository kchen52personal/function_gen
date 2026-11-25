"""Integration probe; uses no model, tokens, real accounts or held-out data."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile

ROOT = Path('/shared/hans/research/website_tool_suite_generator_v1')
FIXTURE = ROOT / 'local_fixtures/ecommerce_storefront_20261117'
WRAPPER = Path(__file__).with_name('sandbox_fixture_v1.py')
OUTPUT = FIXTURE / 'fixture_sandbox_probe_20261117_v1.json'

CHILD = r'''
import json, os, socket, urllib.request
from pathlib import Path
out = {}
with urllib.request.urlopen('http://127.0.0.1:18765/', timeout=4) as response:
    out['fixture_http'] = response.status
    out['fixture_has_html'] = b'<html' in response.read().lower()
Path(os.environ['WORKSPACE_DIR'], 'test_canary.txt').write_text('ok')
out['workspace_write'] = True
try:
    Path('/shared/hans/AGENTS.md').read_text()
    out['outside_read'] = 'allowed'
except OSError as exc: out['outside_read'] = type(exc).__name__
for name, address in [('host_loopback', ('127.0.0.1', int(os.environ['SENTINEL_PORT']))),
                      ('external_network', ('1.1.1.1', 443))]:
    try:
        with socket.create_connection(address, timeout=2): out[name] = 'connected'
    except OSError as exc: out[name] = type(exc).__name__
print(json.dumps(out))
'''


def main():
    # The wrapper deliberately strips all environment variables, including the
    # sentinel port; pass the port in code, not via untrusted inherited env.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        sentinel_port = listener.getsockname()[1]
        child = CHILD.replace("int(os.environ['SENTINEL_PORT'])", str(sentinel_port))
        with tempfile.TemporaryDirectory(prefix='.fixture_agent_', dir=ROOT) as workspace:
            env = {'WORKSPACE_DIR': workspace, 'FIXTURE_ROOT': str(FIXTURE)}
            run = subprocess.run(['/shared/miniconda3/bin/python', str(WRAPPER),
                '/shared/miniconda3/bin/python', '-c', child], env=env, capture_output=True,
                text=True, timeout=40, check=False)
            try:
                result = json.loads(run.stdout)
            except ValueError:
                result = {'output_parse_error': True}
            result.update(returncode=run.returncode, stderr=run.stderr[:2000])
            result['passed'] = (run.returncode == 0 and result.get('fixture_http') == 200
                and result.get('fixture_has_html') is True and result.get('workspace_write') is True
                and result.get('outside_read') == 'PermissionError'
                and result.get('host_loopback') != 'connected'
                and result.get('external_network') != 'connected')
    with OUTPUT.open('x') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
