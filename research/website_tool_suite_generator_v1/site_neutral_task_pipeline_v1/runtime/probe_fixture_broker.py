"""End-to-end local namespace + fake broker check; no API calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path('/shared/hans/research/website_tool_suite_generator_v1')
RUNTIME = Path(__file__).resolve().parent
sys.path[:0] = [str(RUNTIME), str(ROOT / 'pipeline_codex_v73')]
import tool_broker

FIXTURE = ROOT / 'local_fixtures/ecommerce_storefront_20261117'
OUTPUT = FIXTURE / 'fixture_broker_probe_20261117_v1.json'
CHILD = '''
import json, urllib.request
from broker_tool_model import BrokerToolModel
text = BrokerToolModel()(messages=[{'role':'user','content':'local probe'}])
with urllib.request.urlopen('http://127.0.0.1:18765/', timeout=5) as response:
    print(json.dumps({'broker_reply': text, 'fixture_http': response.status}))
'''


def main():
    # A short workspace path is necessary for Linux AF_UNIX pathname limits.
    with tempfile.TemporaryDirectory(prefix='.fb_', dir=ROOT) as directory:
        workspace = Path(directory)
        shutil.copyfile(RUNTIME / 'broker_tool_model.py', workspace / 'broker_tool_model.py')
        tool_broker._logger = lambda _: None
        with tool_broker.Broker(workspace, workspace, pathname_socket=True) as broker:
            broker.call = lambda request: 'fake-broker-response'
            run = subprocess.run(['/shared/miniconda3/bin/python', str(RUNTIME / 'sandbox_fixture_v1.py'),
                '/shared/miniconda3/bin/python', '-c', CHILD],
                env={'WORKSPACE_DIR': str(workspace), 'FIXTURE_ROOT': str(FIXTURE)},
                capture_output=True, text=True, timeout=40, check=False)
            try:
                result = json.loads(run.stdout)
            except ValueError:
                result = {'parse_error': True}
            result.update(returncode=run.returncode, stderr=run.stderr[:2000])
            result['passed'] = (run.returncode == 0 and result.get('fixture_http') == 200
                                and result.get('broker_reply') == 'fake-broker-response')
        result['broker_socket_cleaned'] = not (workspace / '.broker.sock').exists()
        result['passed'] = result['passed'] and result['broker_socket_cleaned']
    with OUTPUT.open('x') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
