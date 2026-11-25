import os
import sys
import time
import threading

from codex_bounded_process import run_bounded


def test_small_output_and_exit_code_are_preserved(tmp_path):
    code, out, err = run_bounded(
        [sys.executable, '-c', 'import sys; print("hello"); print("oops", file=sys.stderr); sys.exit(7)'],
        cwd=tmp_path, env=dict(os.environ), timeout=5, max_output_bytes=1024)
    assert (code, out, err) == (7, 'hello\n', 'oops\n')


def test_output_limit_terminates_noisy_process_without_unbounded_capture(tmp_path):
    code, out, err = run_bounded(
        [sys.executable, '-c', 'import os;\nwhile True: os.write(1, b"x" * 65536)'],
        cwd=tmp_path, env=dict(os.environ), timeout=5, max_output_bytes=2048)
    assert code == 125
    assert len(out.encode()) <= 2048
    assert 'output limit' in err


def test_timeout_terminates_process(tmp_path):
    started = time.monotonic()
    code, _, err = run_bounded(
        [sys.executable, '-c', 'import time; time.sleep(10)'],
        cwd=tmp_path, env=dict(os.environ), timeout=0.2, max_output_bytes=1024)
    assert code == 124 and 'timed out' in err
    assert time.monotonic() - started < 5


def test_external_cancellation_stops_a_process_and_records_why(tmp_path):
    cancelled = threading.Event()
    timer = threading.Timer(0.2, cancelled.set)
    timer.start()
    started = time.monotonic()
    try:
        code, _, err = run_bounded(
            [sys.executable, '-c', 'import time; time.sleep(10)'],
            cwd=tmp_path, env=dict(os.environ), timeout=10,
            cancel_requested=cancelled.is_set,
            cancel_reason='inner-tool budget exhausted')
    finally:
        timer.cancel()
    assert code == 126 and 'inner-tool budget exhausted' in err
    assert time.monotonic() - started < 5
