"""Bounded, spend-logged model access for confined WebWright inner tools."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import socketserver
import secrets
import sys
import threading
from urllib.parse import urlparse
from uuid import uuid4
from api_propose import _failed_usage, _logger, _model_used, _post_json, _resolve_api_key, _response_text, _safe_detail_log
from local_propose import SPEND_LEDGER, _append_jsonl
from event_log import emit

ENDPOINT = 'https://yibuapi.com/v1/chat/completions'
KEY = Path('/shared/hans/.secrets/website_tool_api_key')


def _model_event(root, event, **metadata):
    try:
        emit(root/'model_events.jsonl', 'webwright_inner_model', event, **metadata)
    except (OSError, ValueError, TypeError) as error:
        print('inner model timeline unavailable: '+type(error).__name__, file=sys.stderr, flush=True)


def chat_messages(messages: object) -> list[dict]:
    if not isinstance(messages, list) or len(messages) > 8:
        raise ValueError('invalid messages')
    output = []
    image_count = 0
    for message in messages:
        if not isinstance(message, dict) or message.get('role') not in ('system', 'user', 'assistant'):
            raise ValueError('invalid role')
        content = message.get('content')
        if isinstance(content, str):
            output.append({'role': message['role'], 'content': content[:100000]})
            continue
        if not isinstance(content, list) or len(content) > 12:
            raise ValueError('invalid content')
        parts = []
        for part in content:
            if not isinstance(part, dict):
                raise ValueError('invalid content part')
            if part.get('type') in ('text', 'input_text'):
                parts.append({'type': 'text', 'text': str(part.get('text', ''))[:100000]})
            elif part.get('type') == 'input_image':
                image = part.get('image_url')
                if not isinstance(image, str) or not image.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,')):
                    raise ValueError('only local PNG/JPEG data is accepted')
                image_count += 1
                if image_count > 4 or len(image) > 4_000_000:
                    raise ValueError('image budget exceeded')
                parts.append({'type': 'image_url', 'image_url': {'url': image}})
            else:
                raise ValueError('unsupported content')
        output.append({'role': message['role'], 'content': parts})
    return output


class Broker:
    def __init__(self, workspace: Path, root: Path, limit: int = 6, model: str = 'qwen3.5-plus',
                 pathname_socket: bool = False):
        if model not in {'qwen3.5-plus', 'qwen3.8-flash', 'gpt-5.4'}:
            raise ValueError('unsupported inner-tool model')
        address = 'ww_' + secrets.token_hex(16)
        socket_path = (workspace.resolve() / '.broker.sock') if pathname_socket else None
        if socket_path and len(str(socket_path).encode()) >= 108:
            raise ValueError('broker workspace path is too long for a Unix socket')
        self.root = root
        self.limit = limit
        self.model = model
        self.count = 0
        self.lock = threading.Lock()
        # Inform the outer runner only when a request is actually denied. A
        # reflection may use its last permitted call and still finish normally.
        self.budget_exhausted = threading.Event()
        self.logger = _logger(root / 'tool_api_call.json')
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    data = self.rfile.readline(16_000_001)
                    if len(data) > 16_000_000:
                        raise ValueError('request too large')
                    result = {'text': owner.call(json.loads(data))}
                except Exception as exc:
                    result = {'error': f'{type(exc).__name__}: {str(exc)[:300]}'}
                self.wfile.write(json.dumps(result).encode() + b'\n')

        self.server = socketserver.ThreadingUnixStreamServer(str(socket_path) if socket_path else '\0' + address, Handler)
        self.socket_path = socket_path
        if socket_path:
            socket_path.chmod(0o600)
            stat = socket_path.lstat()
            self.socket_identity = (stat.st_dev, stat.st_ino)
        (workspace / '.broker_address').write_text('path:' + str(socket_path) if socket_path else address, encoding='utf-8')
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.socket_path:
            try:
                stat = self.socket_path.lstat()
                if (stat.st_dev, stat.st_ino) == self.socket_identity:
                    self.socket_path.unlink()
            except FileNotFoundError:
                pass

    def call(self, request: dict) -> str:
        payload = {'model': self.model, 'messages': chat_messages(request['messages']),
                   'max_tokens': max(1, min(int(request.get('max_output_tokens', 2048)), 2048)), 'stream': False}
        with self.lock:
            if self.count >= self.limit:
                self.budget_exhausted.set()
                _model_event(self.root, 'request_rejected', reason='inner_call_budget_exhausted',
                             call_limit=self.limit)
                raise RuntimeError('inner-tool model call budget exhausted')
            self.count += 1
            call_index = self.count
        import time
        started = time.monotonic()
        operation_id = uuid4().hex
        _model_event(self.root, 'request_started', operation_id=operation_id, call_index=call_index,
                     call_limit=self.limit, requested_model=payload['model'], provider=urlparse(ENDPOINT).netloc)
        response = {}
        error = None
        try:
            response = _post_json(ENDPOINT, _resolve_api_key(KEY), payload, 180)
        except Exception as exc:
            error = exc
        elapsed = (time.monotonic() - started) * 1000
        usage = _failed_usage(response)
        model_used = _model_used(response, payload['model'])
        _model_event(self.root, 'request_finished', operation_id=operation_id, call_index=call_index,
                     status='failed' if error else 'completed', error_type=type(error).__name__ if error else None,
                     duration_ms=round(elapsed, 3), requested_model=payload['model'], returned_model=model_used,
                     input_tokens=usage['input_tokens'], output_tokens=usage['output_tokens'],
                     cached_input_tokens=usage['cached_input_tokens'], reasoning_output_tokens=usage['reasoning_output_tokens'],
                     total_tokens=usage['total_tokens'])
        detail_error = _safe_detail_log(self.logger, model=model_used, request_data=payload,
            response_data=response or {'usage': {}}, user='hans_webwright_inner_tool',
            duration_ms=elapsed, success=error is None, error=type(error).__name__ if error else None)
        _append_jsonl(SPEND_LEDGER, {'experiment_artifact_path': str(self.root),
            'experiment_stage': 'webwright_inner_tool', 'timestamp': datetime.now(timezone.utc).isoformat(),
            'model': model_used, 'requested_model': payload['model'], 'provider': urlparse(ENDPOINT).netloc,
            'in': usage['input_tokens'], 'out': usage['output_tokens'], 'total_tokens': usage['total_tokens'],
            'cached_in': usage['cached_input_tokens'], 'reasoning_out': usage['reasoning_output_tokens'],
            'usd': None, 'cost_source': 'monthly_quota_rate_not_exposed', 'billing_mode': 'monthly_quota',
            'status': 'api_failure' if error else 'completed', 'error_type': type(error).__name__ if error else None,
            'detail_log_error_type': detail_error})
        if error:
            raise error
        return _response_text(response)
