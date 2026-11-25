"""WebWright inner-tool model: forward only messages through a local broker."""
from __future__ import annotations
import json
from pathlib import Path
import socket
from types import SimpleNamespace


class BrokerToolModel:
    def __init__(self, *, model_name='qwen3.5-plus', request_timeout_seconds=180, **kwargs):
        self.config = SimpleNamespace(model_name=model_name, openrouter_endpoint='local-broker')
        self.timeout = min(int(request_timeout_seconds), 180)

    def __call__(self, messages, *, max_output_tokens=2048, **kwargs):
        workspace = Path(__file__).resolve().parent
        address = (workspace / '.broker_address').read_text(encoding='utf-8').strip()
        if address.startswith('path:'):
            target = Path(address[5:])
            if target != workspace / '.broker.sock':
                raise ValueError('invalid filesystem broker address')
            address = str(target)
        else:
            address = '\0' + address
        payload = json.dumps({'messages': messages, 'max_output_tokens': max_output_tokens}).encode() + b'\n'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.timeout + 15)
            connection.connect(address)
            connection.sendall(payload)
            stream = connection.makefile('rb')
            result = json.loads(stream.readline(1_000_000))
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result['text']
