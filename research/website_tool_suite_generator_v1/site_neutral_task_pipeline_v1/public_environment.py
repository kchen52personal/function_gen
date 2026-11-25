"""Generic public-site adapter: existing pipeline, isolated browser + HTTPS relay.

No website selectors, authored development examples, answer rules or legacy
function are supplied. Public-site task factory/synthesizer are unchanged.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
import threading
from urllib.parse import urlparse
from uuid import uuid4
import yaml

import development
import factory
import synthesis
from process_identity import current_identity
from runtime.public_egress import PublicEgress
from runtime.tool_broker import Broker
from private_session import session_file
from attempt_diagnostics import collect as collect_attempt_diagnostics
from event_log import emit
from api_timing import summarize as summarize_api_timing
from telemetry_summary import summarize as summarize_telemetry
from codex_bounded_process import run_bounded

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
RUNTIME = HERE/'runtime'
SHELL = RUNTIME/'docker_public_shell.py'


def snapshot(source, destination):
    """Retain bounded regular artifacts, never sockets or symlinks."""
    destination.mkdir(parents=True, exist_ok=False)
    total, count = 0, 0
    for path in source.rglob('*'):
        if path.is_symlink() or not path.is_file():
            continue
        total += path.stat().st_size
        count += 1
        if total > 250_000_000 or count > 2500:
            raise ValueError('artifact snapshot limit exceeded; original workspace preserved')
        target = destination/path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def workspace():
    path = PROJECT/('pub_'+uuid4().hex[:12])
    path.mkdir(exist_ok=False)
    (path/'.container_bin').mkdir()
    (path/'.container_bin/python').symlink_to('/usr/bin/python3')
    (path/'.container_bin/python3').symlink_to('/usr/bin/python3')
    return path


def cleanup_commands(work):
    """Only containers labelled with this exact, newly allocated workspace."""
    if PROJECT not in work.resolve().parents:
        raise ValueError('cleanup workspace outside project')
    found = subprocess.run(['/usr/bin/docker', 'ps', '-q', '--no-trunc', '--filter',
                            f'label=hans.public.workspace={work}'],
                           capture_output=True, text=True, timeout=10, check=True)
    for cid in found.stdout.splitlines():
        if not re.fullmatch(r'[0-9a-f]{64}', cid):
            raise ValueError('invalid cleanup container id')
        subprocess.run(['/usr/bin/docker', 'stop', '--time', '2', cid],
                       capture_output=True, text=True, timeout=10, check=False)


class IsolatedPublicEnvironment:
    name = 'isolated_public_https_site'
    agent_calls, inner_calls, command_seconds = 40, 6, 900
    limitations = [
        'Network-disabled non-root containers; only the host-selected HTTPS site/subdomains through a public-IP-checked relay.',
        'TLS is not intercepted; read-only operations remain a prompt constraint, not an HTTP-method guarantee.',
        'Third-party resources are blocked and may affect site behavior.',
        'Browser Playwright version differs from the earlier host-based arXiv pilot.',
        'Fresh browser per command; writable workspace has no aggregate disk quota; not a complete security audit.',
    ]

    def __init__(self, root, url, *, agent_steps=40, agent_calls=40, inner_calls=6,
                 allow_media_playback=False, resource_hosts=(), session_directory=None,
                 raw_request_argument=False, generation_batch_size=0, generation_resume_checkpoint=None,
                 distinct_entities=False, parallel_generation_development=False, diagnostic_head_requests=False,
                 diagnostic_public_get=False, agent_model='qwen3.5-plus', allow_authorized_creation=False,
                 development_workers=7):
        factory.validate_site(url, 'Public read-only browser operation')
        if agent_model not in {'qwen3.5-plus','qwen3.8-flash'}:
            raise ValueError('unsupported public development agent model')
        self.agent_model=agent_model
        if type(development_workers) is not int or not 1 <= development_workers <= 7:
            raise ValueError('development_workers must be an integer from 1 to 7')
        self.development_workers=development_workers
        self.allow_authorized_creation=bool(allow_authorized_creation)
        if self.allow_authorized_creation and not session_directory:
            raise ValueError('authorized creation requires a user-approved session')
        if (type(agent_steps) is not int or not 1<=agent_steps<=100 or type(agent_calls) is not int
            or not agent_steps<=agent_calls<=110 or type(inner_calls) is not int or not 1<=inner_calls<=16):
            raise ValueError('invalid bounded model/step budgets')
        if len(resource_hosts)>4:raise ValueError('too many extra resource domains')
        for domain in resource_hosts:factory.validate_site('https://'+domain+'/', 'Public website dependency')
        self.root, self.url = Path(root), url
        self.host = urlparse(url).hostname.lower()
        self.agent_steps,self.agent_calls,self.inner_calls=agent_steps,agent_calls,inner_calls
        self.allow_media_playback=bool(allow_media_playback)
        self.raw_request_argument=bool(raw_request_argument)
        if type(generation_batch_size) is not int or not 0<=generation_batch_size<=16:
            raise ValueError('invalid generation batch size')
        self.generation_batch_size=generation_batch_size
        self.generation_resume_checkpoint=generation_resume_checkpoint
        self.distinct_entities=bool(distinct_entities)
        self.parallel_generation_development=bool(parallel_generation_development)
        self.diagnostic_head_requests=bool(diagnostic_head_requests)
        self.diagnostic_public_get=bool(diagnostic_public_get)
        if self.diagnostic_public_get:self.diagnostic_head_requests=True
        if self.parallel_generation_development and not generation_batch_size:
            raise ValueError('parallel development requires batched generation')
        self._cancel=threading.Event()
        self._executor=None
        self._prestarted=None
        self.session_path = session_file(session_directory, url) if session_directory else None
        self.resource_hosts=tuple(domain.lower() for domain in resource_hosts)
        self.limitations=list(type(self).limitations)
        if self.resource_hosts:
            self.limitations[0]='Network-disabled containers; HTTPS relay permits the selected site/subdomains and explicitly recorded content domains, with public-IP checks: '+', '.join(self.resource_hosts)
        if self.session_path:
            self.limitations += [
                'Fresh contexts restore one authenticated baseline; session expiry can still block tasks.',
                'Session state stays outside workspace snapshots, but browser code can access its active cookies; not a credential-isolation guarantee.',
                'Authenticated browsers use bounded 512 tasks/threads and 3 GiB RAM after the login renderer hit the 192-task limit.']

    def command_environment(self, work):
        env = dict(os.environ, WORKSPACE_DIR=str(work))
        for key in ('WW_PRIVATE_SESSION_FILE', 'WW_SESSION_WEBSITE', 'WW_PUBLIC_LOGIN_MODE',
                    'WW_BROWSER_SESSION_STATE'):
            env.pop(key, None)
        if self.session_path:
            env.update(WW_PRIVATE_SESSION_FILE=str(self.session_path), WW_SESSION_WEBSITE=self.url)
        return env

    def validate_site(self, url, idea):
        factory.validate_site(url, idea)
        if url != self.url:
            raise ValueError('environment cannot change host-selected website')

    def run_command(self, work, args, log, seconds=180, *, login_bootstrap=False):
        env = self.command_environment(work)
        if login_bootstrap:
            env['WW_PUBLIC_LOGIN_MODE'] = '1'
        try:
            with PublicEgress(work, self.host, log, resource_hosts=self.resource_hosts):
                return subprocess.run([str(SHELL), *args], env=env, capture_output=True,
                                      text=True, timeout=seconds, check=False)
        finally:
            cleanup_commands(work)

    async def reconnaissance(self, url, output, max_pages=3):
        output.mkdir(parents=True, exist_ok=False)
        work = workspace()
        shutil.copyfile(RUNTIME/'public_recon_probe.py', work/'probe.py')
        result = await asyncio.to_thread(self.run_command, work, ['python3', str(work/'probe.py'), url],
                                         output/'network.jsonl', 180)
        (output/'stdout.log').write_text(result.stdout)
        (output/'stderr.log').write_text(result.stderr)
        snapshot(work, output/'browser_artifacts')
        if result.returncode:
            raise RuntimeError('isolated public reconnaissance failed; inspect recon logs')
        data = json.loads((work/'recon.json').read_text())
        (output/'recon.json').write_text(json.dumps(data, indent=2)+'\n')
        return data

    def create_taskset(self,url,idea,recon,output,**kwargs):
        if self.allow_authorized_creation:kwargs['allow_authorized_creation']=True
        return factory.create_taskset(url,idea,recon,output,allow_media_playback=self.allow_media_playback,
                                      raw_request_argument=self.raw_request_argument,
                                      generation_batch_size=self.generation_batch_size,
                                      generation_resume_checkpoint=self.generation_resume_checkpoint,
                                      distinct_entities=self.distinct_entities,**kwargs)

    def propose_replacements(self,url,contract,prior,registry,count,round_index,output):
        opts={'allow_authorized_creation':True} if self.allow_authorized_creation else {}
        return factory.propose_replacements(url,contract,prior,registry,count,round_index,output,
                                           allow_media_playback=self.allow_media_playback,**opts)

    def synthesize(self,url,contract,examples,output):
        opts={'allow_authorized_creation':True} if self.allow_authorized_creation else {}
        return synthesis.synthesize(url,contract,examples,output,allow_media_playback=self.allow_media_playback,**opts)

    def event(self,stage,event,**metadata):
        return emit(self.root/'events.jsonl',stage,event,**metadata)

    @staticmethod
    def batch_signature(url,contract,tasks,output):
        return hashlib.sha256(json.dumps([url,contract,tasks,str(output)],
            ensure_ascii=False,sort_keys=True).encode()).hexdigest()

    def prestart_development(self,url,contract,tasks,output,**kwargs):
        if not self.parallel_generation_development or self._prestarted is not None:
            raise ValueError('unexpected duplicate/disabled development prestart')
        signature=self.batch_signature(url,contract,tasks,output)
        self.event('development','parallel_worker_queued',batch_signature=signature,
                   task_ids=[t['task_id'] for t in tasks],artifact_path=str(output))
        self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='development')
        self._prestarted=(signature,self._executor.submit(
            self._run_development_actual,url,contract,tasks,output,**kwargs))

    def close(self, *, drain_development=False):
        # A frozen development batch has its own bounded queue and per-attempt
        # limits. Let it finish when held-set generation fails, preserving its
        # artifacts; an explicit interruption still uses normal cancellation.
        if not drain_development:
            self._cancel.set()
        if self._executor is not None:
            self._executor.shutdown(wait=True,cancel_futures=not drain_development)
            self.event('development','parallel_worker_joined',drained=drain_development)

    def run_development(self, url, contract, tasks, output, *, attempt_timeout, queue_timeout):
        if self._prestarted is not None:
            signature,future=self._prestarted
            if self.batch_signature(url,contract,tasks,output)!=signature:
                raise ValueError('prestarted development batch/contract changed')
            self._prestarted=None
            self.event('development','parallel_worker_awaited',batch_signature=signature)
            return future.result()
        return self._run_development_actual(url,contract,tasks,output,
            attempt_timeout=attempt_timeout,queue_timeout=queue_timeout)

    def _run_development_actual(self, url, contract, tasks, output, *, attempt_timeout, queue_timeout):
        if self.development_workers == 1 or len(tasks) < 2:
            return self._run_development_sequential(url, contract, tasks, output,
                attempt_timeout=attempt_timeout, queue_timeout=queue_timeout)
        return self._run_development_parallel(url, contract, tasks, output,
            attempt_timeout=attempt_timeout, queue_timeout=queue_timeout)

    def _run_development_parallel(self, url, contract, tasks, output, *, attempt_timeout, queue_timeout):
        """Run independent one-task batches concurrently and aggregate in input order.

        Per-worker directories, events, network relays and summaries stay intact.
        Controller-created relative symlinks maintain the historical lookup path.
        """
        ids = [task['task_id'] for task in tasks]
        if len(ids) != len(set(ids)) or any(not isinstance(t, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', t) for t in ids):
            raise ValueError('parallel development requires unique simple task IDs')
        output = Path(output)
        output.mkdir(parents=True, exist_ok=False)
        workers_root = output/'.parallel_batches'
        workers_root.mkdir()
        started = time.monotonic()
        deadline = started + queue_timeout
        state = {'status': 'running', 'attempts': [], 'process_identity': current_identity(),
                 'selection': 'formal WebWright submission only; no independent verifier vote',
                 'development_workers': min(self.development_workers, len(tasks)),
                 'agent_call_cap': self.agent_calls, 'agent_step_cap': self.agent_steps,
                 'inner_call_cap': self.inner_calls, 'media_playback_allowed': self.allow_media_playback,
                 'resource_hosts': self.resource_hosts, 'parallel_errors': []}
        records = {}
        def save():
            state['attempts'] = [records[t] for t in ids if t in records]
            tmp = output/'.summary.tmp'
            tmp.write_text(json.dumps(state, indent=2)+'\n')
            tmp.replace(output/'summary.json')
        def run_one(task):
            remaining = deadline - time.monotonic()
            if self._cancel.is_set() or remaining < 90:
                return None
            worker = workers_root/task['task_id']
            self._run_development_sequential(url, contract, [task], worker,
                attempt_timeout=attempt_timeout, queue_timeout=remaining)
            return worker
        save()
        executor = ThreadPoolExecutor(max_workers=min(self.development_workers, len(tasks)),
                                      thread_name_prefix='development-task')
        futures = {executor.submit(run_one, task): task['task_id'] for task in tasks}
        try:
            for future in as_completed(futures):
                task_id = futures[future]
                try:
                    worker = future.result()
                    if worker is not None:
                        result = json.loads((worker/'summary.json').read_text())
                        rows = result.get('attempts', [])
                        if len(rows) > 1 or any(row.get('task_id') != task_id for row in rows):
                            raise ValueError('invalid isolated worker result')
                        if rows:
                            records[task_id] = rows[0]
                            (output/task_id).symlink_to(Path('.parallel_batches')/task_id/task_id, target_is_directory=True)
                except Exception as exc:
                    state['parallel_errors'].append({'task_id': task_id, 'error_type': type(exc).__name__})
                save()
        except BaseException:
            self._cancel.set()
            state['status'] = 'interrupted'
            save()
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=self._cancel.is_set())
        state.update(status='cancelled' if self._cancel.is_set() else
                     'failed' if state['parallel_errors'] else 'attempts_complete',
                     elapsed_seconds=round(time.monotonic()-started, 2))
        save()
        if state['parallel_errors']:
            raise RuntimeError('one or more parallel development workers failed; see summary')
        return output

    def _run_development_sequential(self, url, contract, tasks, output, *, attempt_timeout, queue_timeout):
        output.mkdir(parents=True, exist_ok=False)
        state = {'status': 'running', 'attempts': [], 'process_identity': current_identity(),
                 'selection': 'formal WebWright submission only; no independent verifier vote',
                 'agent_call_cap': self.agent_calls, 'agent_step_cap':self.agent_steps,
                 'inner_call_cap': self.inner_calls, 'media_playback_allowed':self.allow_media_playback,
                 'resource_hosts':self.resource_hosts}
        def save():
            temp = output/'.summary.tmp'
            temp.write_text(json.dumps(state, indent=2)+'\n')
            temp.replace(output/'summary.json')
        started = time.monotonic()
        save()
        for task in tasks:
            if self._cancel.is_set():break
            remaining = queue_timeout - (time.monotonic()-started)
            if remaining < 90:
                break
            root = output/task['task_id']/self.agent_model
            root.mkdir(parents=True)
            work = workspace()
            shutil.copyfile(RUNTIME/'broker_tool_model.py', work/'broker_tool_model.py')
            (work/'tool_model.yaml').write_text(yaml.safe_dump({'model': {
                'model_class': 'broker_tool_model.BrokerToolModel', 'model_name': 'qwen3.5-plus',
                'request_timeout_seconds': 180}}))
            config = development.build_task_config(task, root, self.agent_model, contract,
                                                   state_changing=self.allow_authorized_creation)
            if self.diagnostic_head_requests:
                config['environment']['environment_class']=('webwright_safe_environment.PublicGetDiagnosticWorkspaceEnvironment'
                    if self.diagnostic_public_get else 'webwright_safe_environment.DiagnosticToolWorkspaceEnvironment')
            if self.allow_authorized_creation:
                config['environment']['environment_class'] = (
                    'side_effect_environment.StateChangingPublicGetWorkspaceEnvironment' if self.diagnostic_public_get else
                    'side_effect_environment.StateChangingDiagnosticWorkspaceEnvironment' if self.diagnostic_head_requests else
                    'side_effect_environment.StateChangingWorkspaceEnvironment')
            config['model']['max_api_calls'] = self.agent_calls
            config['agent']['step_limit'] = self.agent_steps
            config['agent']['system_template'] = config['agent']['system_template'].replace('12-call cap', f'{self.inner_calls}-call cap')
            if self.allow_authorized_creation:
                template=config['agent']['system_template']
                old='Never log in, purchase, add to a cart, or change account/site state.'
                if old not in template:raise ValueError('expected default public safety instructions missing')
                config['agent']['system_template']=template.replace(old,
                    'Use the existing user-approved session. Only the item creation explicitly requested in the task is authorized; do not modify or delete existing items, share content, purchase, or change account settings.').replace(
                    'this is a public read-only task.', 'this is a user-authorized item creation task.')
                config['agent']['system_template']+='\nCreation may persist or autosave. Do not create extra items. Report unavailable options and errors honestly.\n'
            if self.allow_media_playback:
                template=config['agent']['system_template']
                old='Never log in, purchase, add to a cart, or change account/site state.'
                if old not in template:raise ValueError('expected default public safety instructions missing')
                config['agent']['system_template']=template.replace(old,
                    'Transient media playback is authorized. Never log in, purchase, add to a cart, change accounts or saved playlists, download media, or bypass restrictions.').replace(
                    'this is a public read-only task.', 'this is public browsing with explicitly authorized media playback.')
                config['agent']['system_template']+='\nA play-button click alone is not playback proof. Record observed player identity and media paused/error/currentTime before and after a bounded wait where accessible. Do not invoke private media APIs or fake media state. Treat preview-only, login-required, regional or unavailable playback honestly; do not skip the first song result.\n'
            config['agent']['system_template'] += (
                '\nBrowser commands run in fresh isolated containers. Only the original HTTPS site, its '
                'subdomains and any host-configured content domains are reachable. Do not install packages or use direct HTTP clients for task results. Inspect actual '
                'DOM/accessibility names after locator failures. Extract answers from the page, not constants. '
                'Complete final_script.py, run it, perform the built-in self-check, and formally submit within '
                'the request budget. Do not spend all requests exploring without submitting.\n')
            if self.diagnostic_head_requests:
                config['agent']['system_template'] += ('\nFor connectivity diagnostics only, a standalone '
                    'curl -I https://site/path command is available; it reports only HTTP status, not headers. '
                    'Do not combine this command with others or print credentials. Browser Playwright '
                    'remains responsible for completing the user task.\n')
            if self.diagnostic_public_get:
                config['agent']['system_template'] += ('\nYou may also use standalone curl https://site/path '
                    'for unauthenticated public page diagnostics; responses are bounded. Do not include '
                    'credentials or private APIs. Direct requests are diagnostics and cannot establish '
                    'browser playback; use Playwright for the user-facing task.\n')
            if self.session_path:
                config['agent']['system_template'] += (
                    '\nThe runtime automatically restores a user-approved authenticated baseline into each '
                    'new browser context. Do not read, print, export or modify cookies, tokens, storage_state '
                    'or the authentication file. Do not log in again or modify the account. If the login '
                    'expires, stop and report the blocker. This session provides access only, never answers.\n')
            config['environment']['shell'] = str(SHELL)
            config['environment']['command_timeout_seconds'] = 960
            config_path = root/'config.yaml'
            config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
            task_text = task['intent']+'\nExact development parameters: '+json.dumps(task['parameters'], ensure_ascii=False)
            command = [sys.executable, str(RUNTIME/'launch_agent_v3.py'), '--task', task_text,
                       '--task-id', task['task_id'], '--workspace', str(work), '--config', str(config_path),
                       '--start-url', url]
            env = self.command_environment(work)
            env.update(PYTHONPATH=f'{HERE}:{PROJECT / "pipeline_codex_v73"}:{PROJECT / "vendor/Webwright/src"}:{RUNTIME}',
                       PYTHONDONTWRITEBYTECODE='1',
                       CODEX_COMMAND_TIMELINE_PATH=str(root/'command_events.jsonl'),
                       CODEX_MODEL_TIMELINE_PATH=str(root/'model_events.jsonl'))
            state['current'] = {'task_id': task['task_id'], 'workspace': str(work)}
            (root/'task_request.json').write_text(json.dumps(task,ensure_ascii=False,indent=2)+'\n')
            save()
            self.event('development','attempt_started',task_id=task['task_id'],
                request_path=str(root/'task_request.json'),artifact_path=str(root),
                timeout_seconds=min(attempt_timeout,remaining),agent_steps=self.agent_steps,
                agent_calls=self.agent_calls,inner_calls=self.inner_calls)
            attempt_start = time.monotonic()
            code,stdout,stderr,inner=125,'','',0
            runner_error=None
            archive_errors=[]
            try:
                with PublicEgress(work, self.host, root/'network.jsonl', resource_hosts=self.resource_hosts), Broker(
                        work, root, limit=self.inner_calls, model='qwen3.5-plus', pathname_socket=True) as broker:
                    code, stdout, stderr = run_bounded(command, cwd=PROJECT, env=env,
                        timeout=min(attempt_timeout, remaining),
                        cancel_requested=lambda:self._cancel.is_set() or broker.budget_exhausted.is_set(),
                        cancel_reason='bounded worker cancelled')
                    inner = broker.count
                    if code==126:
                        stderr+='\n'+('controller cancellation' if self._cancel.is_set()
                            else 'inner-tool model budget exhausted')+'\n'
            except BaseException as exc:
                runner_error=exc
                code=130 if isinstance(exc,KeyboardInterrupt) else 124 if isinstance(exc,TimeoutError) else 125
                stderr+='\nRunner exception: '+type(exc).__name__+'\n'
            finally:
                try:
                    cleanup_commands(work)
                except Exception as exc:
                    archive_errors.append('container_cleanup:'+type(exc).__name__)
            (root/'stdout.log').write_text(stdout)
            (root/'stderr.log').write_text(stderr)
            try:
                snapshot(work, root/'agent')
            except Exception as exc:
                archive_errors.append('artifact_snapshot:'+type(exc).__name__)
            trajectory = work/'trajectory.json'
            try:
                info = json.loads(trajectory.read_text()).get('info', {}) if trajectory.is_file() else {}
                if not isinstance(info,dict):
                    info={};archive_errors.append('invalid_trajectory_info')
            except (ValueError,OSError):
                info={};archive_errors.append('incomplete_or_unreadable_trajectory')
            diagnostics=collect_attempt_diagnostics(work,code,info,stderr,
                type(runner_error).__name__ if runner_error else None)
            diagnostics['archive_errors']=archive_errors
            diagnostics['controller_cancelled']=self._cancel.is_set()
            diagnostics['api_timing']=summarize_api_timing(root/'api_logs/stats')
            telemetry_path = root/'timing_summary.json'
            try:
                telemetry = summarize_telemetry(root, time.monotonic()-attempt_start)
                telemetry_path.write_text(json.dumps(telemetry, ensure_ascii=False, indent=2)+'\n')
                diagnostics['timing_summary'] = str(telemetry_path)
            except (OSError, ValueError, TypeError) as exc:
                archive_errors.append('timing_summary:'+type(exc).__name__)
            (root/'failure_diagnostics.json').write_text(json.dumps(diagnostics,ensure_ascii=False,indent=2)+'\n')
            script = root/'agent/final_script.py'
            row = {'task_id': task['task_id'], 'process_exit': code, 'trajectory_status': info.get('exit_status'),
                   'agent_calls': info.get('api_calls'), 'inner_calls': inner,
                   'elapsed_seconds': round(time.monotonic()-attempt_start, 2), 'original_workspace': str(work),
                   'failure_diagnostics':str(root/'failure_diagnostics.json'),
                   'last_saved_step':diagnostics['last_saved_step'],
                   'failure_signal_counts':diagnostics['signal_counts'],
                   'runner_error_type':diagnostics['runner_error_type'],'archive_errors':archive_errors}
            if script.is_file() and not script.is_symlink():
                row['script_sha256'] = hashlib.sha256(script.read_bytes()).hexdigest()
            state['attempts'].append(row)
            state.pop('current', None)
            save()
            self.event('development','attempt_finished',task_id=task['task_id'],process_exit=code,
                trajectory_status=info.get('exit_status'),elapsed_seconds=row['elapsed_seconds'],
                agent_calls=row['agent_calls'],inner_calls=inner,
                diagnostics_path=row['failure_diagnostics'],archive_errors=archive_errors)
            if runner_error is not None:
                raise runner_error
        state.update(status='cancelled' if self._cancel.is_set() else 'attempts_complete',
                     elapsed_seconds=round(time.monotonic()-started, 2))
        save()
        return output

    def run_ungraded_case(self, task, script, output):
        output.mkdir(parents=True, exist_ok=False)
        work = workspace()
        (output/'task_request.json').write_text(json.dumps(task,ensure_ascii=False,indent=2)+'\n')
        started=time.monotonic()
        operation=self.event('held_execution','case_started',task_id=task['task_id'],
            request_path=str(output/'task_request.json'),artifact_path=str(output),
            script_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),timeout_seconds=180)
        shutil.copyfile(script, work/'candidate.py')
        flags = [part for key, value in task['parameters'].items() for part in (f'--{key}', factory.cli_value(value))]
        code,stdout,stderr,error=125,'','',None
        archive_errors=[]
        try:
            result = self.run_command(work, ['python3', str(work/'candidate.py'), *flags,
                                      '--output-dir', str(work/'execution')], output/'network.jsonl', 180)
            code,stdout,stderr=result.returncode,result.stdout,result.stderr
        except BaseException as exc:
            error=exc
            code=124 if isinstance(exc,subprocess.TimeoutExpired) else 130 if isinstance(exc,KeyboardInterrupt) else 125
            stdout=getattr(exc,'stdout','') or ''
            stderr=getattr(exc,'stderr','') or ''
            if isinstance(stdout,bytes):stdout=stdout.decode(errors='replace')
            if isinstance(stderr,bytes):stderr=stderr.decode(errors='replace')
            stderr+='\nRunner exception: '+type(exc).__name__+'\n'
        finally:
            (output/'stdout.log').write_text(stdout)
            (output/'stderr.log').write_text(stderr)
            try:snapshot(work,output/'workspace')
            except Exception as exc:archive_errors.append(type(exc).__name__)
        diagnostics=collect_attempt_diagnostics(work,code,{},stderr,type(error).__name__ if error else None)
        diagnostics['archive_errors']=archive_errors
        (output/'failure_diagnostics.json').write_text(json.dumps(diagnostics,ensure_ascii=False,indent=2)+'\n')
        state = {'task_id': task['task_id'], 'status': 'ran_ungraded' if code == 0 else 'execution_failed',
                 'process_exit': code, 'semantic_verdict': None,'original_workspace':str(work),
                 'elapsed_seconds':round(time.monotonic()-started,3),
                 'runner_error_type':type(error).__name__ if error else None,'archive_errors':archive_errors,
                 'reason': 'Frozen function executed without WebWright solving the held task; no correctness verdict.'}
        (output/'summary.json').write_text(json.dumps(state, indent=2)+'\n')
        self.event('held_execution','case_finished',operation_id=operation,task_id=task['task_id'],
                   process_exit=code,elapsed_seconds=state['elapsed_seconds'],
                   error_type=state['runner_error_type'],archive_errors=archive_errors)
        if error is not None and not isinstance(error,Exception):raise error
        return state
