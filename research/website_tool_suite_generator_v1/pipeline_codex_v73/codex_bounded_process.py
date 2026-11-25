"""Bound both time and captured output for untrusted generated processes."""
from __future__ import annotations

import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from typing import Any, Callable


def run_bounded(command: list[str], *, cwd: Path, env: dict[str, str], timeout: float,
                max_output_bytes: int = 4 * 1024 * 1024,
                cancel_requested: Callable[[], bool] | None = None,
                cancel_reason: str = 'process cancelled') -> tuple[int, str, str]:
    if timeout <= 0 or max_output_bytes < 1:
        raise ValueError('timeout and output limit must be positive')
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
    selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
    buffers: dict[str, bytearray] = {'stdout': bytearray(), 'stderr': bytearray()}
    deadline = time.monotonic() + timeout
    failure: str | None = None
    def kill_group() -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        while selector.get_map():
            if cancel_requested is not None and cancel_requested():
                failure = cancel_reason
                kill_group()
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = 'process timed out'
                kill_group()
                break
            for key, _ in selector.select(min(remaining, 0.25)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = buffers[key.data]
                room = max_output_bytes - len(buffer)
                buffer.extend(chunk[:max(0, room)])
                if len(chunk) > room:
                    failure = f'{key.data} exceeded the {max_output_bytes}-byte output limit'
                    kill_group()
                    break
            if failure:
                break
        if failure is None:
            while process.poll() is None:
                if cancel_requested is not None and cancel_requested():
                    failure = cancel_reason
                    kill_group()
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = 'process timed out'
                    kill_group()
                    break
                try:
                    process.wait(timeout=min(remaining, 0.25))
                except subprocess.TimeoutExpired:
                    pass
        if failure is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                kill_group()
                process.wait()
        if failure:
            code = 124 if failure == 'process timed out' else (126 if failure == cancel_reason else 125)
        else:
            code = process.returncode
        stdout = bytes(buffers['stdout']).decode('utf-8', errors='replace')
        stderr = bytes(buffers['stderr']).decode('utf-8', errors='replace')
        if failure:
            stderr += '\n' + failure + '\n'
        return int(code), stdout, stderr
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
        if process.poll() is None:
            kill_group()
            process.wait()
