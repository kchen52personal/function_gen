"""Fixture-only isolated browser-observer/candidate experiment, no real account.

The browser and observer run in one disposable container, while a host-authored
mock candidate runs in a separate container sharing only the fixture network.
The host captures CDP screenshots as bytes and signs them after one action.
This is not WebWright, a production harness, or proof against CDP/page spoofing.
"""
import base64
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time
import threading
from uuid import uuid4
from datetime import datetime,timezone

import evidence_capsule as cap
from trace_quality import assess_trace,check_action_image_digests,measure_forwarding_delays,reflect_trace_status
from runtime.fixture_session import FixtureSession, IMAGE, PROJECT

HERE=Path(__file__).parent
RUNTIME=HERE/'runtime'
SNAP=PROJECT/'automated_runs/container_compat_20261117_v2'
DOCKER='/usr/bin/docker'


def command(args,timeout=30,check=True):
    return subprocess.run(args,capture_output=True,timeout=timeout,check=check)


def check_snapshot():
    # Narrow read-only mounts only. Reject sockets and symlinks that might make
    # a supposedly static dependency snapshot expose host resources.
    for root in (SNAP/'deps', SNAP/'vendor'):
        if not root.is_dir() or root.is_symlink(): raise ValueError('invalid dependency root')
        for directory,dirs,files in os.walk(root,followlinks=False):
            for name in dirs+files:
                path=Path(directory)/name; mode=path.lstat().st_mode
                if stat.S_ISLNK(mode) or stat.S_ISSOCK(mode):
                    raise ValueError('dependency snapshot contains symlink or socket')


def start_container(site_id,role,marker=None,cdp_url=None,*,isolate=False,bridge_dir=None):
    name='ww-observer-prototype-'+role+'-'+uuid4().hex[:12]
    network='none' if isolate and role=='candidate' else 'container:'+site_id
    args=[DOCKER,'run','--detach','--rm','--pull=never','--name',name,
          '--network',network,'--cap-drop','ALL','--security-opt','no-new-privileges',
          '--pids-limit','192','--cpus','1','--memory','1536m','--read-only','--shm-size','256m',
          '--tmpfs','/tmp:rw,exec,nosuid,nodev,size=256m','--user',f'{os.getuid()}:{os.getgid()}',
          '--mount',f'type=bind,src={SNAP / "deps"},dst=/opt/deps,readonly',
          '--mount',f'type=bind,src={SNAP / "vendor"},dst=/opt/vendor,readonly',
          '--env','PYTHONPATH=/opt/deps:/opt/vendor','--env','PLAYWRIGHT_BROWSERS_PATH=/ms-playwright',
          '--env','PYTHONDONTWRITEBYTECODE=1','--env','XDG_CONFIG_HOME=/tmp/xdg-config',
          '--env','XDG_CACHE_HOME=/tmp/xdg-cache']
    if isolate:
        mode=',readonly' if role=='candidate' else ''
        args+=['--mount',f'type=bind,src={bridge_dir},dst=/bridge{mode}']
    if marker is not None: args+=['--env','CAPSULE_DEMO_PROTECTED_MARKER='+str(marker)]
    if cdp_url is not None: args+=['--env','CAPSULE_DEMO_CDP_URL='+cdp_url]
    args+=['--entrypoint','/usr/bin/timeout',IMAGE,'240s','sleep','240']
    result=command(args,timeout=20)
    cid=result.stdout.decode().strip()
    if not re.fullmatch(r'[0-9a-f]{64}',cid): raise ValueError('invalid Docker container id')
    return cid


def script_exec(cid,source,*,detached=False,timeout=45):
    flags=['-d'] if detached else []
    return command([DOCKER,'exec',*flags,cid,'python3','-c',source],timeout=timeout)


def read_observer(cid,source,*,reload=False):
    if reload:
        raw=command([DOCKER,'exec','-e','FIXTURE_OBSERVER_RELOAD=1',cid,'python3','-c',source],timeout=45).stdout
    else:
        raw=script_exec(cid,source,timeout=20).stdout
    value=json.loads(raw)
    png=base64.b64decode(value.pop('png'),validate=True)
    if not png.startswith(b'\x89PNG\r\n\x1a\n') or len(png)>cap.MAX_ARTIFACT_BYTES:
        raise ValueError('bad screenshot')
    return value,png


def drain_proxy(proc,output,events,captures,capture_ready,stream_done):
    """Keep fixture screenshot bytes out of the diagnostic JSONL trace."""
    for line in iter(proc.stdout.readline,b''):
        try:
            captured=False
            item=json.loads(line)
            if not isinstance(item,dict):continue
            if item.get('event')=='action_capture':
                encoded=item.pop('png',None)
                data=base64.b64decode(encoded,validate=True) if isinstance(encoded,str) else b''
                if (len(captures)>=3 or not data.startswith(b'\x89PNG\r\n\x1a\n') or
                    len(data)>cap.MAX_ARTIFACT_BYTES):
                    item={'event':'action_capture_invalid'}
                else:
                    name=f'action_capture_{len(captures):02d}.png'
                    with (output/name).open('xb') as stream:stream.write(data)
                    captures.append(data)
                    item.update(file=name,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))
                    captured=True
            if len(events)<10000:events.append(item)
            elif len(events)==10000:events.append({'event':'trace_overflow'})
            if captured:capture_ready.set()
            if item.get('event')=='client_stream_end':stream_done.set()
        except (OSError,ValueError,TypeError,json.JSONDecodeError):
            if len(events)<10000:events.append({'event':'trace_parse_failure'})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',choices=['normal','spoof_label','spoof_with_click','unsupported_http','keyboard_search','mixed_search','two_clicks'],default='normal')
    parser.add_argument('--trace',action='store_true')
    parser.add_argument('--bypass',action='store_true',help='mock candidate bypasses diagnostic trace')
    parser.add_argument('--isolate',action='store_true',help='fixture-only socket-mediated network separation')
    parser.add_argument('--action-capture',action='store_true',help='capture at a mouse press on fixture only')
    args=parser.parse_args()
    if args.bypass and not args.trace:parser.error('--bypass requires --trace')
    if args.isolate and not args.trace:parser.error('--isolate requires --trace')
    if args.action_capture and (not args.trace or not args.isolate):parser.error('--action-capture requires --trace --isolate')
    output=PROJECT/'automated_runs'/('verifier_observer_isolation_20261124_'+uuid4().hex[:8])
    output.mkdir(mode=0o700)
    marker=output/'protected_marker.txt';marker.write_bytes(b'host-only-marker')
    initial_marker=marker.read_bytes()
    state={'status':'starting','case':args.case,'trace_requested':args.trace,'bypass_requested':args.bypass,
        'network_isolation_requested':args.isolate,
        'action_capture_requested':args.action_capture,
        'fixture_only':True,'candidate_type':'host-authored mock',
        'eligible_for_pipeline_gate':False,'held_tasks_opened':False,'replayed':False,
        'limitations':['Not a WebWright trajectory or existing Playwright function.',
           'A candidate with CDP access can manipulate the page; images alone cannot prove truth.',
           'Other browsers/network interactions are not monitored; execution provenance is incomplete.',
           'Read-only dependency snapshots are trusted; container isolation is not a complete security audit.',
           'Relay is diagnostic, not a general security boundary or proof of action correctness.',
           'Fixture-only network isolation is not yet audited or integrated into real WebWright.']}
    def checkpoint(stage):
        state['stage']=stage
        state['last_update_utc']=datetime.now(timezone.utc).isoformat()
        (output/'summary.json').write_text(json.dumps(state,indent=2)+'\n')
    checkpoint('snapshot_audit')
    owner=candidate=owner_proc=proxy_proc=bridge_proc=proxy_thread=None
    events=[];action_captures=[];capture_ready=threading.Event();stream_done=threading.Event()
    try:
        check_snapshot()
        owner_code=(RUNTIME/'fixture_observer_browser.py').read_text()
        observe_code=(RUNTIME/'fixture_observer_capture.py').read_text()
        proxy_code=(RUNTIME/'fixture_cdp_trace_proxy.py').read_text()
        bridge_code=(RUNTIME/'fixture_unix_tcp_bridge.py').read_text()
        candidate_name={'normal':'fixture_observer_candidate.py','spoof_label':'fixture_observer_spoof_candidate.py',
                        'spoof_with_click':'fixture_observer_spoof_click_candidate.py',
                        'unsupported_http':'fixture_observer_http_probe.py',
                        'keyboard_search':'fixture_observer_keyboard_candidate.py',
                        'mixed_search':'fixture_observer_mixed_candidate.py',
                        'two_clicks':'fixture_observer_two_click_candidate.py'}[args.case]
        candidate_path=RUNTIME/candidate_name
        candidate_code=candidate_path.read_text()
        checkpoint('fixture_start')
        with FixtureSession('cart_v2') as fixture:
            try:
                checkpoint('container_start')
                bridge_dir=output/'socket_bridge'
                if args.isolate:bridge_dir.mkdir(mode=0o700)
                owner=start_container(fixture.container,'browser',isolate=args.isolate,bridge_dir=bridge_dir)
                url='http://127.0.0.1:19223' if args.trace and not args.bypass else 'http://127.0.0.1:19222'
                candidate=start_container(fixture.container,'candidate',marker,url,isolate=args.isolate,bridge_dir=bridge_dir)
                owner_proc=subprocess.Popen([DOCKER,'exec',owner,'python3','-c',owner_code],
                                            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                if args.trace:
                    proxy_cmd=[DOCKER,'exec']
                    if args.isolate:proxy_cmd+=['-e','CAPSULE_PROXY_UNIX_SOCKET=/bridge/cdp.sock']
                    if args.action_capture:proxy_cmd+=['-e','CAPSULE_FIXTURE_CAPTURE_ACTION=1']
                    proxy_cmd += [owner,'python3','-c',proxy_code]
                    proxy_proc=subprocess.Popen(proxy_cmd,
                                                stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                    proxy_thread=threading.Thread(target=drain_proxy,
                        args=(proxy_proc,output,events,action_captures,capture_ready,stream_done),daemon=True)
                    proxy_thread.start()
                if args.isolate:
                    bridge_proc=subprocess.Popen([DOCKER,'exec',candidate,'python3','-c',bridge_code],
                                                stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                checkpoint('observer_readiness')
                before=before_png=None
                readiness_errors=[]
                deadline=time.monotonic()+45
                while time.monotonic()<deadline:
                    if owner_proc.poll() is not None:
                        raise RuntimeError('browser owner exited before readiness')
                    try:
                        before,before_png=read_observer(owner,observe_code)
                        break
                    except (subprocess.SubprocessError,ValueError,KeyError) as exc:
                        detail={'error_type':type(exc).__name__}
                        if isinstance(exc,subprocess.CalledProcessError):
                            detail['stderr_tail']=(exc.stderr or b'').decode(errors='replace')[-1200:]
                        readiness_errors.append(detail)
                        time.sleep(1)
                state['readiness_attempts']=len(readiness_errors)+int(before is not None)
                if readiness_errors: state['last_readiness_error']=readiness_errors[-1]
                if before is None: raise RuntimeError('observer readiness failed')
                checkpoint('candidate_single_action')
                try:
                    answer=json.loads(script_exec(candidate,candidate_code,timeout=45).stdout)
                except subprocess.CalledProcessError as exc:
                    state['candidate_error']={'error_type':type(exc).__name__,
                        'stderr_tail':(exc.stderr or b'').decode(errors='replace')[-2000:]}
                    raise RuntimeError('mock candidate failed') from exc
                checkpoint('after_capture')
                after,after_png=read_observer(owner,observe_code)
                reloaded=reloaded_png=None
                if args.action_capture:
                    capture_ready.wait(timeout=5)
                else:
                    # Extra local-fixture-only navigation: never enable blindly for
                    # real tasks, where reloading can itself have side effects.
                    checkpoint('fixture_reload_diagnostic')
                    reloaded,reloaded_png=read_observer(owner,observe_code,reload=True)
                state.update(before=before,after=after,candidate_report=answer,
                    fixture_reload_observation=reloaded,
                    marker_unchanged=marker.read_bytes()==initial_marker,
                    screenshot_hashes_differ=hashlib.sha256(before_png).digest()!=hashlib.sha256(after_png).digest())
                # Integrity-only capsule; it asserts no browser authenticity or
                # candidate completion, even when all hashes match.
                intent=({'keyboard_search':'Search the fixture using the keyboard.',
                         'mixed_search':'Click search, type a query and submit it.',
                         'unsupported_http':'Attempt an unsupported debug HTTP route.',
                         'two_clicks':'Click one demo product add button, then click site navigation.'}.get(
                            args.case,'Click one demo product add button once.'))
                task={'task_id':'fixture_one_action','intent':intent}
                key=secrets.token_bytes(32)
                captures=[{'role':'before','data':before_png},{'role':'after','data':after_png}]
                if args.action_capture and action_captures:
                    captures.extend({'role':'action','data':data} for data in action_captures)
                elif reloaded_png is not None:
                    captures.append({'role':'observation','data':reloaded_png})
                state['action_capture_count']=len(action_captures)
                if args.trace:state['trace_stream_closed_observed']=stream_done.wait(timeout=5)
                trace_events=list(events)
                if args.action_capture:state['action_trace_binding']=check_action_image_digests(trace_events,action_captures)
                trace_snapshot=''.join(json.dumps(x,sort_keys=True)+'\n' for x in trace_events).encode()
                (output/'trace_prefix.jsonl').write_bytes(trace_snapshot)
                evidence_metadata={'trace_prefix_sha256':hashlib.sha256(trace_snapshot).hexdigest(),
                                   'trace_scope':'observed_prefix_not_complete'}
                cap.create_capsule(output/'signed_capsule',run_id='fixture_observer_run',task=task,
                    script=candidate_code.encode(),captures=captures,key=key,evidence_metadata=evidence_metadata)
                state['integrity']=cap.verify_capsule(output/'signed_capsule',run_id='fixture_observer_run',
                    task=task,script=candidate_code.encode(),key=key,
                    expected_metadata={'trace_prefix_sha256':hashlib.sha256((output/'trace_prefix.jsonl').read_bytes()).hexdigest(),
                                       'trace_scope':'observed_prefix_not_complete'})
                if args.action_capture and (state['action_trace_binding']['status']!='matched_not_proven' or
                                            not state.get('trace_stream_closed_observed')):
                    state.update(status='incomplete_evidence',reason='action-time image missing or not matched to the trace')
                else:
                    state['status']='complete'
            finally:
                checkpoint('container_cleanup')
                if proxy_proc:time.sleep(1)
                state['container_cleanup']={}
                for label,cid in [('candidate',candidate),('browser',owner)]:
                    if cid:
                        try:
                            logs=command([DOCKER,'logs',cid],timeout=8,check=False)
                            (output/(label+'_container.log')).write_bytes((logs.stdout+logs.stderr)[-12000:])
                            result=command([DOCKER,'stop','--time','2',cid],timeout=12,check=False)
                            state['container_cleanup'][label]=result.returncode
                        except subprocess.SubprocessError:
                            state['container_cleanup'][label]='stop_failed'
                if owner_proc:
                    try:
                        stdout,stderr=owner_proc.communicate(timeout=5)
                        (output/'browser_process.log').write_bytes((stdout+stderr)[-12000:])
                    except subprocess.TimeoutExpired:
                        state['container_cleanup']['browser_process']='still_running'
                if proxy_proc:
                    try:
                        proxy_proc.wait(timeout=5)
                        if proxy_thread:proxy_thread.join(timeout=5)
                        stderr=proxy_proc.stderr.read()
                        (output/'cdp_trace.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in events))
                        (output/'proxy_process.log').write_bytes(stderr[-12000:])
                        methods=[x.get('method') for x in events if x.get('event')=='cdp_method']
                        state['trace_summary']={'events':len(events),'method_events':len(methods),
                            'mouse_dispatch_events':sum(x=='Input.dispatchMouseEvent' for x in methods),
                            'runtime_call_events':sum(x=='Runtime.callFunctionOn' for x in methods),
                            'uninspected_frames':sum(x.get('event')=='uninspected_frame' for x in events),
                            'unparsed_text_frames':sum(x.get('event')=='unparsed_text_frame' for x in events),
                            'blocked_requests':sum(x.get('event')=='request_blocked' for x in events),
                            'action_captures':sum(x.get('event')=='action_capture' for x in events),
                            'capture_errors':sum(x.get('event') in {'action_capture_failed','action_capture_skipped',
                                'action_capture_invalid','trace_parse_failure','trace_overflow'} for x in events),
                            'relay_errors':sum(x.get('event')=='relay_error' for x in events),
                            'limitation':'Optional proxy can be bypassed; counts do not prove action or success.'}
                        state['trace_quality']=assess_trace(events,network_isolated=args.isolate,
                            action_capture_requested=args.action_capture,require_forwarding_marker=True)
                        state['proxy_forwarding_delays']=measure_forwarding_delays(events)
                        reflect_trace_status(state,action_capture_requested=args.action_capture)
                    except subprocess.TimeoutExpired:
                        state['container_cleanup']['proxy_process']='still_running'
                if bridge_proc:
                    try:
                        stdout,stderr=bridge_proc.communicate(timeout=5)
                        (output/'bridge_process.log').write_bytes((stdout+stderr)[-12000:])
                    except subprocess.TimeoutExpired:
                        state['container_cleanup']['bridge_process']='still_running'
    except Exception as exc:
        state.update(status='failed',error_type=type(exc).__name__,reason=str(exc)[:300])
    reflect_trace_status(state,action_capture_requested=args.action_capture)
    checkpoint('finished')
    print(json.dumps({'output':str(output),'status':state['status'],'error_type':state.get('error_type'),
                      'cleanup':state.get('container_cleanup')}),flush=True)


if __name__=='__main__':main()
