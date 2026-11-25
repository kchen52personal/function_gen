"""Regression tests for optional pathname IPC; public mode remains abstract."""
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE / 'runtime'), str(ROOT / 'pipeline_codex_v73')]
import tool_broker


def read_reply(address):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(address)
        client.sendall(b'{"messages":[]}\n')
        return json.loads(client.makefile('rb').readline())


@pytest.mark.parametrize('pathname', [False, True])
def test_both_broker_modes(monkeypatch, pathname):
    monkeypatch.setattr(tool_broker, '_logger', lambda _: None)
    with tempfile.TemporaryDirectory(prefix='.bt_', dir=ROOT) as directory:
        workspace = Path(directory)
        with tool_broker.Broker(workspace, workspace, pathname_socket=pathname) as broker:
            broker.call = lambda request: 'stubbed'
            address = (workspace/'.broker_address').read_text()
            assert address.startswith('path:') is pathname
            assert read_reply(address[5:] if pathname else '\0'+address) == {'text':'stubbed'}
        assert not (workspace/'.broker.sock').exists()


def test_client_rejects_unexpected_pathname():
    with tempfile.TemporaryDirectory(prefix='.bt_', dir=ROOT) as directory:
        workspace = Path(directory)
        module_path = workspace/'broker_tool_model.py'
        shutil.copyfile(HERE/'runtime/broker_tool_model.py', module_path)
        (workspace/'.broker_address').write_text('path:/tmp/not-our-broker')
        spec = importlib.util.spec_from_file_location('probe_broker_client', module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with pytest.raises(ValueError, match='invalid filesystem broker address'):
            module.BrokerToolModel()(messages=[])


def test_fixture_sandbox_is_fail_closed_after_security_finding():
    result = subprocess.run([sys.executable, str(HERE/'runtime/sandbox_fixture_v1.py'),
                             '/bin/true'], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'host Unix socket isolation failed' in result.stderr
