"""Fail-safe local workspace environment for website-tool WebWright runs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from cuawright.webwright.environments.local_workspace import LocalWorkspaceEnvironment
from result_log import read_result
from uniform_reference_check import validate_tool_output
from codex_artifact_guard import checked_file
from codex_checkpoint import save_checkpoint
from codex_screenshots import ensure_screenshots
from codex_bounded_process import run_bounded


def _trace_event(event: str, **metadata: Any) -> None:
    """Optional host-owned metadata timeline; never log command text or page data."""
    path = os.environ.get('CODEX_COMMAND_TIMELINE_PATH')
    if not path:
        return
    try:
        from event_log import emit
        emit(Path(path), 'webwright_command', event, **metadata)
    except (OSError, ImportError, ValueError, TypeError) as error:
        print('command timeline unavailable: ' + type(error).__name__, file=sys.stderr, flush=True)


def structural_list_advisory(start_url: str, schema: dict, case: dict, value: dict,
                             workspace: Path, browser_path: str) -> dict:
    """Run only on a disclosed, single-limit list case; never on held inputs."""
    if len(case) != 1:
        return {'status': 'inapplicable'}
    name, limit = next(iter(case.items()))
    if not re.search(r'(?:^|_)(?:limit|count)$', name, re.I) or type(limit) is not int or not 2 <= limit <= 20:
        return {'status': 'inapplicable'}
    fields = [key for key, kind in schema.items() if str(kind).casefold().replace(' ', '') == 'list[str]']
    result = value.get('result') if isinstance(value, dict) else None
    if len(fields) != 1 or not isinstance(result, dict) or not isinstance(result.get(fields[0]), list):
        return {'status': 'inapplicable'}
    payload = {'start_url': start_url, 'source_url': value.get('source_url'),
               'items': result[fields[0]], 'limit': limit}
    env = {'PATH': '/shared/miniconda3/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
           'PLAYWRIGHT_BROWSERS_PATH': browser_path,
           'TMPDIR': str(workspace / '.tmp'), 'XDG_CACHE_HOME': str(workspace / '.cache'),
           'XDG_CONFIG_HOME': str(workspace / '.config'), 'PYTHONDONTWRITEBYTECODE': '1'}
    try:
        run = subprocess.run([sys.executable, str(Path(__file__).with_name('codex_list_advisory.py'))],
                             input=json.dumps(payload, ensure_ascii=False), cwd=workspace, env=env,
                             text=True, capture_output=True, timeout=45, check=False)
        if run.returncode != 0:
            return {'status': 'unavailable', 'reason': 'advisory process failed'}
        return json.loads(run.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {'status': 'unavailable', 'reason': type(error).__name__}


def _safe_head_probe(command: str) -> bool:
    """Single HTTPS HEAD diagnostic, without shell expansion or credential flags.

    The container's existing egress relay enforces the destination host. This
    syntactic check is an additional constraint, not a general shell sandbox.
    """
    if any(ch in command for ch in ('\n', '\r', ';', '|', '&', '$', '`', '>', '<', '\\')):
        return False
    try:
        args=shlex.split(command)
        if not args or args[0]!='curl' or len(args)<3:
            return False
        flags=args[1:-1]
        if not any(flag in ('-I','--head') for flag in flags):
            return False
        if any(flag not in ('-I','--head','-s','--silent','-S','--show-error','--fail') for flag in flags):
            return False
        url=urlsplit(args[-1])
        return (url.scheme=='https' and bool(url.hostname) and not url.username
                and not url.password and url.port in (None,443) and not url.query and not url.fragment)
    except (ValueError,TypeError):
        return False


def _safe_public_get(command: str) -> bool:
    """Standalone public GET; the host relay enforces destinations, not HTTP methods."""
    if any(ch in command for ch in ('\n','\r',';','|','&','$','`','>','<','\\')):
        return False
    try:
        args=shlex.split(command)
        if len(args)<2 or args[0]!='curl':return False
        if any(flag not in ('-s','--silent','-S','--show-error','--fail','-L','--location')
               for flag in args[1:-1]):return False
        url=urlsplit(args[-1])
        if (url.scheme!='https' or not url.hostname or url.username or url.password
                or url.port not in (None,443) or url.fragment):return False
        if re.search(r'(?i)(?:^|[?&])(?:token|key|api[_-]?key|password|secret|session|auth|cookie)=', '?'+url.query):
            return False
        return True
    except (ValueError,TypeError):
        return False


def _unsafe_reasons(command: str, *, allow_head_probe: bool = False, allow_public_get: bool = False) -> list[str]:
    """Return fail-closed reasons for commands that cannot help a headless run."""

    normalized = " ".join(command.casefold().split())
    reasons: list[str] = []
    if any(
        token in normalized
        for token in (
            "playwright codegen",
            "playwright open",
            "playwright install",
            "pip install",
            "pip3 install",
        )
    ):
        reasons.append(
            "interactive or install commands are unavailable; use the installed "
            "browser through a headless Python script"
        )
    if ".show()" in normalized:
        reasons.append("GUI image viewers are unavailable; inspect saved artifacts instead")
    if "full_page=true" in normalized or "full_page = true" in normalized:
        reasons.append("full-page screenshots are prohibited; use a bounded viewport or clip")
    if (any(token in normalized for token in ("requests.get(", "requests.post(", "curl ", "wget "))
            and not (allow_head_probe and _safe_head_probe(command))
            and not (allow_public_get and _safe_public_get(command))):
        reasons.append("direct HTTP clients are prohibited; use the visible Playwright website")
    return reasons


class SafeToolWorkspaceEnvironment(LocalWorkspaceEnvironment):
    """Reject known-unusable or evidence-destroying browser commands."""

    def _final_script_path(self) -> Path:
        path = super()._final_script_path()
        try:
            if path.exists() or path.is_symlink():
                checked_file(self._workspace_dir(), path)
        except (OSError, ValueError):
            # The parent environment previews this path in observations. Never
            # let it follow an untrusted symlink out of the workspace.
            return self._workspace_dir() / '.codex_rejected_script'
        return path

    def execute(self, action: dict[str, Any], cwd: str = "") -> dict[str, Any]:
        command = str(
            action.get("command")
            or action.get("bash_command")
            or action.get("python_code")
            or ""
        )
        operation_id = uuid4().hex
        step = getattr(self, '_step_index', 0) + 1
        started = time.monotonic()
        _trace_event('step_started', operation_id=operation_id, step=step,
                     command_sha256=hashlib.sha256(command.encode()).hexdigest(),
                     command_bytes=len(command.encode()))
        try:
            result = self._execute_timed(action, cwd, command, operation_id, step)
        except BaseException as error:
            _trace_event('step_finished', operation_id=operation_id, step=step,
                         status='exception', error_type=type(error).__name__,
                         duration_seconds=round(time.monotonic()-started, 3))
            raise
        _trace_event('step_finished', operation_id=operation_id, step=step,
                     status='completed', returncode=result.get('returncode'),
                     duration_seconds=round(time.monotonic()-started, 3))
        return result

    def _execute_timed(self, action, cwd, command, operation_id, step):
        reasons = _unsafe_reasons(command, allow_head_probe=getattr(self, 'allow_head_probe', False),
                                  allow_public_get=getattr(self, 'allow_public_get', False))
        if reasons:
            rejected = dict(action)
            message = "command rejected by safe WebWright environment: " + "; ".join(reasons)
            rejected["command"] = f"printf '%s\\n' {message!r}; exit 2"
            rejected["bash_command"] = rejected["command"]
            _trace_event('command_rejected', operation_id=operation_id, step=step,
                         reason_count=len(reasons))
            return self._timed_super_execute(rejected, cwd, operation_id, step)
        if getattr(self, 'allow_head_probe', False) and _safe_head_probe(command):
            # Return only HTTP status; do not surface Set-Cookie or other
            # potentially sensitive response headers in observations/logs.
            url=shlex.split(command)[-1]
            probe = "curl -sS -I -o /dev/null -w 'HTTP status: %{http_code}\\n' --max-time 30 " + shlex.quote(url)
            action=dict(action, command=probe, bash_command=probe)
        elif getattr(self, 'allow_public_get', False) and _safe_public_get(command):
            url=shlex.split(command)[-1]
            redirects=' -L' if any(x in ('-L','--location') for x in shlex.split(command)[1:-1]) else ''
            probe=('curl -sS'+redirects+' --max-time 30 --max-filesize 262144 '
                   '--range 0-262143 '+shlex.quote(url))
            action=dict(action,command=probe,bash_command=probe)
        result = self._timed_super_execute(action, cwd, operation_id, step)
        smoke_started = time.monotonic()
        _trace_event('smoke_check_started', operation_id=operation_id, step=step)
        try:
            smoke = self._smoke_changed_script()
        except BaseException as error:
            _trace_event('smoke_check_finished', operation_id=operation_id, step=step,
                         status='exception', error_type=type(error).__name__,
                         duration_seconds=round(time.monotonic()-smoke_started, 3))
            raise
        smoke_status = ('not_run' if smoke is None else 'static_only' if smoke.startswith('STATIC CHECK ONLY')
                        else 'failed' if smoke.startswith(('FAIL', 'STATIC CHECK FAILED')) or ': FAIL:' in smoke
                        else 'passed_format_or_checkpoint')
        _trace_event('smoke_check_finished', operation_id=operation_id, step=step,
                     status=smoke_status, duration_seconds=round(time.monotonic()-smoke_started, 3))
        if smoke is not None:
            text = f'\nAUTOMATIC VISIBLE-CASE SMOKE TEST:\n{smoke}'
            result['output'] = (result.get('output', '') + text)[-12000:]
            result['observation']['command_output'] = (
                result['observation'].get('command_output', '') + text)[-12000:]
        return result

    def _timed_super_execute(self, action, cwd, operation_id, step):
        started = time.monotonic()
        _trace_event('command_started', operation_id=operation_id, step=step)
        try:
            result = super().execute(action, cwd=cwd)
        except BaseException as error:
            _trace_event('command_finished', operation_id=operation_id, step=step,
                         status='exception', error_type=type(error).__name__,
                         duration_seconds=round(time.monotonic()-started, 3))
            raise
        _trace_event('command_finished', operation_id=operation_id, step=step,
                     status='completed', returncode=result.get('returncode'),
                     output_chars=len(result.get('output') or ''),
                     duration_seconds=round(time.monotonic()-started, 3))
        return result
    def _smoke_changed_script(self) -> str | None:
        """Run a changed script on disclosed inputs only, inside the sandbox."""
        visible_json = self.config.env.get('CODEX_VISIBLE_CASE_JSON')
        schema_json = self.config.env.get('CODEX_RETURN_SCHEMA_JSON')
        script = self._final_script_path()
        if not visible_json or not schema_json or not script.is_file():
            return None
        try:
            digest = hashlib.sha256(script.read_bytes()).hexdigest()
            if digest == getattr(self, '_codex_smoked_digest', None):
                return None
            self._codex_smoked_digest = digest
            case, schema = json.loads(visible_json), json.loads(schema_json)
            if not isinstance(case, dict) or not isinstance(schema, dict):
                return 'FAIL: invalid visible-case configuration'
            base = self._workspace_dir() / 'codex_smoke' / f'step_{self._step_index:04d}'
            extra = json.loads(self.config.env.get('CODEX_EXTRA_VISIBLE_CASE_JSON', 'null'))
            if extra is not None and not isinstance(extra, dict):
                return 'FAIL: invalid disclosed-boundary configuration'
            visible_cases = [('default', case, base)]
            if extra is not None:
                visible_cases.append(('disclosed_boundary', extra, base / 'disclosed_boundary'))
            observations = []
            for label, current, output in visible_cases:
                flags = [part for key, value in current.items() for part in
                         (f'--{key}', str(value).lower() if isinstance(value, bool) else str(value))]
                command = [self.config.shell, sys.executable, str(script), *flags,
                           '--output-dir', str(output)]
                code, stdout, stderr = run_bounded(
                    command, cwd=self._workspace_dir(),
                    env={**os.environ, 'WORKSPACE_DIR': str(self._workspace_dir())}, timeout=90)
                errors = []
                if code != 0:
                    errors.append(f'process exited {code}')
                try:
                    value = read_result(checked_file(self._workspace_dir(), output / 'final_script_log.txt'))
                    errors.extend(validate_tool_output(
                        str(self.config.start_url), {'returns': schema}, label, value, current))
                except (OSError, ValueError, TypeError) as error:
                    errors.append(f'missing or invalid result log: {error}')
                errors.extend(ensure_screenshots(self._workspace_dir(), output))
                observations.append(f'{label}: ' + ('FAIL: ' + '; '.join(errors) if errors else 'PASS: schema-valid')
                                    + '\n' + (stdout + '\n' + stderr)[-6000:])
                if errors:
                    return '\n'.join(observations)
                if label == 'disclosed_boundary' and self.config.env.get('CODEX_LIST_ADVISORY') == '1':
                    advisory = structural_list_advisory(
                        str(self.config.start_url), schema, current, value, self._workspace_dir(),
                        self.config.env.get('PLAYWRIGHT_BROWSERS_PATH', ''))
                    if advisory.get('status') == 'mismatch':
                        observations.append('STRUCTURAL LIST ADVISORY (heuristic, inspect the task and page): '
                                            + json.dumps(advisory, ensure_ascii=False)[:10000])
            digest = save_checkpoint(self._workspace_dir(), script)
            observations.append(f'PASS: disclosed cases schema-valid; checkpoint {digest[:12]} saved')
            return '\n'.join(observations)
        except (OSError, ValueError, TypeError) as error:
            return f'FAIL: smoke-test setup error: {type(error).__name__}: {error}'


class DiagnosticToolWorkspaceEnvironment(SafeToolWorkspaceEnvironment):
    """Opt in to standalone HEAD checks while preserving the existing controls."""
    allow_head_probe = True


class PublicGetDiagnosticWorkspaceEnvironment(DiagnosticToolWorkspaceEnvironment):
    """Allow bounded unauthenticated public GET diagnostics in addition to HEAD."""
    allow_public_get = True

