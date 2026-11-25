"""Bounded, autonomous task replenishment and frozen checkpoint evaluation.

Only development examples reach the generator/synthesizer. All checkpoint
creation stops before any held-out inputs are opened. Model agreement is
fallible evidence, not ground-truth verification.
"""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from uuid import uuid4

import development
from development_verifier import verify_case
from evaluate import DEFAULT_REFERENCE_MODEL, evaluate_case, run_ungraded_case
from factory import PROJECT, create_taskset, propose_replacements, validate_site
from reconnaissance import reconnaissance
from synthesis import synthesize, trajectory_excerpt
from codex_artifact_guard import checked_file
from process_identity import current_identity
from event_log import emit
from artifact_verification import verify_saved

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save(path, value):
    tmp = path.with_name('.'+path.name+'.'+uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    os.replace(tmp,path)

def select_development_candidate(root, task, record, model='qwen3.5-plus'):
    """Prefer a submitted script, else a hash-checked smoke draft; neither is verified."""
    attempt = root/task['task_id']/model
    workspace = attempt/'agent'
    script = workspace/'final_script.py'
    if not workspace.is_dir() or workspace.is_symlink():
        return None, 'no_candidate'
    try:
        if (record.get('trajectory_status') == 'Submitted' and record.get('process_exit') == 0
            and not script.is_symlink() and digest(checked_file(workspace,script)) == record.get('script_sha256')):
            return script, 'submitted'
    except (OSError, ValueError):
        pass
    try:
        metadata = attempt/'codex_checkpoints/latest.json'
        if not metadata.exists():
            return None, 'no_candidate'
        record = json.loads(checked_file(attempt,metadata).read_text())
        sha = record.get('sha256')
        if not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{64}',sha):
            raise ValueError('invalid checkpoint digest')
        selected = checked_file(attempt,attempt/'codex_checkpoints'/f'{sha}.py')
        if digest(selected) != sha:
            raise ValueError('checkpoint hash mismatch')
    except (ValueError, OSError, TypeError):
        return None, 'invalid_smoke_checkpoint'
    return selected, 'unsubmitted_smoke_checkpoint'

def execute(url, idea, output, *, max_rounds=2, target=8, overall_seconds=21600, final_only=False, webwright_only=False, verify_development=False, environment=None, held_count=4, development_count=8, visual_verification=False):
    # Host-selected environment supplies execution capabilities; model output
    # cannot enable this mode or choose local paths. Public defaults are unchanged.
    if environment is not None and (not webwright_only or not final_only or verify_development):
        raise ValueError('custom environment currently requires submission-only final-only mode')
    validate_fn=environment.validate_site if environment else validate_site
    recon_fn=environment.reconnaissance if environment else reconnaissance
    taskset_fn=environment.create_taskset if environment else create_taskset
    development_fn=environment.run_development if environment else development.run
    synthesis_fn=environment.synthesize if environment else synthesize
    held_fn=environment.run_ungraded_case if environment else run_ungraded_case
    replacement_fn=getattr(environment,'propose_replacements',propose_replacements) if environment else propose_replacements
    validate_fn(url,idea)
    if not 0 <= max_rounds <= 5 or not 2 <= target <= 32:
        raise ValueError('adaptive limits out of range')
    if type(development_count) is not int or not 2<=development_count<=32:
        raise ValueError('development count out of range')
    if type(held_count) is not int or not 1 <= held_count <= 100:
        raise ValueError('held count out of range')
    if webwright_only and not final_only:
        raise ValueError('webwright-only mode requires final-only mode')
    if verify_development and (webwright_only or not final_only):
        raise ValueError('verified development requires final-only and cannot use webwright-only mode')
    if visual_verification and (not webwright_only or environment is None):
        raise ValueError('experimental visual verification requires an isolated submission-only environment')
    output.mkdir(parents=True, exist_ok=False)
    for name in ('.config','.cache','.tmp'):
        (output/name).mkdir()
    os.environ.update(XDG_CONFIG_HOME=str(output/'.config'), XDG_CACHE_HOME=str(output/'.cache'),
                      TMPDIR=str(output/'.tmp'), PLAYWRIGHT_BROWSERS_PATH=str(PROJECT/'.playwright-browsers'))
    started = time.monotonic()
    state = {'status':'reconnaissance_running', 'website':url, 'idea':idea,
        'process_identity':current_identity(),
        'started_at':datetime.now(timezone.utc).isoformat(), 'manual_runtime_review':False,
        'held_opened':False, 'reference_model':None if webwright_only else DEFAULT_REFERENCE_MODEL,
        'development_selection':('webwright_submission_and_independent_assessment' if verify_development else
                                 'webwright_submission_only' if webwright_only else 'webwright_submission_with_diagnostics'),
        'held_evaluation':'ungraded_execution_only' if webwright_only else 'independent_automated_assessment',
        'development_attempts':[], 'rounds':[], 'checkpoints':[],
        'limits':{'initial_development':development_count,'held_out':held_count,'max_replacement_rounds':max_rounds,
                  'target_submitted_trajectories':target,'max_new_development_cases':max_rounds*target,
                  'synthesize_only_after_target':final_only,
                  'overall_seconds':overall_seconds,
                  'per_agent_seconds':development.DEFAULT_ATTEMPT_TIMEOUT_SECONDS,
                  'batch_overhead_seconds_per_task':development.BATCH_OVERHEAD_SECONDS_PER_TASK,
                  'agent_calls':50,'inner_calls':12},
        'limitations':['Model agreement and value grounding are fallible, not verified ground truth.',
          'The code writer and blind reference are from the Qwen family, so their errors may be correlated.',
          'Exact input deduplication does not rule out semantic overlap or pretraining contamination.',
          'The task generator sees original proposals before splitting; later generators do not receive held values.',
          'Repeated checkpoint scores on the same held tasks are exploratory; do not tune on them.',
          'Browser networking is not hard read-only or network-allowlist enforced.']}
    if webwright_only:
        state['limitations'] = [
            'WebWright self-reflection and submission are not independent proof of correctness.',
            'Held tasks are executed without a correctness verifier and cannot yield an accuracy score.',
            'Exact input deduplication does not rule out semantic overlap or pretraining contamination.',
            'The task generator sees original proposals before splitting; later generators do not receive held values.',
            'Browser networking is not hard read-only or network-allowlist enforced.']
    if visual_verification:
        state['limitations'] = [x for x in state['limitations'] if not x.startswith('Held tasks are executed without')]
        state['limitations'].append('Visual verdicts are experimental, uncalibrated and may be incorrect; missing evidence remains inconclusive.')
    summary = output/'summary.json'
    if visual_verification:
        state['visual_verification'] = 'experimental development gate and held-out assessment'
        state['development_selection'] = 'webwright_submission_and_experimental_visual_gate'
        state['held_evaluation'] = 'experimental_visual_assessment'
        state['limitations'].append('Visual verdicts are uncalibrated and may be incorrect; unavailable evidence remains inconclusive.')
    if environment is not None:
        state['execution_environment']=environment.name
        state['fixture_version']=getattr(environment,'fixture_version',None)
        state['cart_changes_allowed']=getattr(environment,'allow_cart',False)
        state['media_playback_allowed']=getattr(environment,'allow_media_playback',False)
        state['authorized_item_creation_allowed']=getattr(environment,'allow_authorized_creation',False)
        state['resource_hosts']=getattr(environment,'resource_hosts',())
        state['authenticated_browser_baseline']=bool(getattr(environment,'session_path',None))
        state['raw_request_argument']=bool(getattr(environment,'raw_request_argument',False))
        state['generation_batch_size']=getattr(environment,'generation_batch_size',0)
        state['diagnostic_head_requests']=bool(getattr(environment,'diagnostic_head_requests',False))
        state['diagnostic_public_get']=bool(getattr(environment,'diagnostic_public_get',False))
        state['distinct_entities']=bool(getattr(environment,'distinct_entities',False))
        state['visual_verifier_role']=('experimental fail-closed development gate and held-out assessment' if visual_verification
                                      else 'retrospective shadow evaluation only; no acceptance or synthesis feedback')
        state['limits'].update(agent_calls=getattr(environment,'agent_calls',40),
                               inner_calls=getattr(environment,'inner_calls',6),
                               command_container_seconds=getattr(environment,'command_seconds',900))
        state['limits']['agent_steps']=getattr(environment,'agent_steps',40)
        state['limitations']=[x for x in state['limitations'] if not x.startswith('Browser networking')]
        if hasattr(environment,'limitations'):
            state['limitations'].extend(environment.limitations)
        else:
            state['limitations'].append('Browser runs in a fixed offline fixture network, not the older public sandbox; isolation is not a complete security audit.')
            state['limitations'].append('This run uses a small fixed local demo; it does not establish public-site performance.')
    last_checkpoint=None
    def checkpoint():
        nonlocal last_checkpoint
        save(summary,state)
        current=(state['status'],len(state['development_attempts']),len(state['rounds']),
                 len(state['checkpoints']),sum(len(c.get('held_results',[])) for c in state['checkpoints']))
        if current!=last_checkpoint:
            emit(output/'events.jsonl','controller','checkpoint',status=state['status'],
                 development_attempts=current[1],replacement_round_records=current[2],
                 checkpoints=current[3],held_results=current[4],held_opened=state['held_opened'])
            last_checkpoint=current
    checkpoint()
    def within_budget():
        return time.monotonic()-started < overall_seconds
    def safe_assessment(task, script, path):
        try:
            return evaluate_case(url, contract, task, script, path)
        except Exception as exc:
            return {'task_id':task['task_id'], 'status':'inconclusive',
                    'reason':'automated assessment raised an exception', 'error_type':type(exc).__name__,
                    'automated_assessment':True, 'independent_human_review':False}
    try:
        recon = asyncio.run(asyncio.wait_for(recon_fn(url,output/'recon',max_pages=3),timeout=240))
        if not any(page.get('http_status') == 200 for page in recon.get('pages',[])):
            state['status']='stopped_site_not_reachable'; checkpoint(); return state
        state['status']='task_generation_running'; checkpoint()
        taskset_kwargs={} if held_count==4 else {'held_count':held_count}
        if development_count!=8:taskset_kwargs['development_count']=development_count
        if getattr(environment,'parallel_generation_development',False):
            def development_ready(early_contract,early_tasks):
                remaining=max(1,int(overall_seconds-(time.monotonic()-started)))
                if remaining<90:raise TimeoutError('overall budget exhausted before parallel development')
                state.update(status='task_generation_and_development_running',
                    parallel_generation_development=True,
                    early_development_manifest_sha256=digest(output/'tasks/development/tasks.json'))
                checkpoint()
                environment.prestart_development(url,early_contract,early_tasks,
                    output/'development_rounds/round_00',
                    attempt_timeout=min(development.DEFAULT_ATTEMPT_TIMEOUT_SECONDS,remaining),
                    queue_timeout=min(len(early_tasks)*(development.DEFAULT_ATTEMPT_TIMEOUT_SECONDS
                        +development.BATCH_OVERHEAD_SECONDS_PER_TASK),remaining))
            taskset_kwargs['on_development_ready']=development_ready
        contract, split = taskset_fn(url,idea,recon,output/'tasks',**taskset_kwargs)
        state.update(contract=contract,split_hashes=split['manifest_sha256'])
        dev_path = output/'tasks/development/tasks.json'
        if digest(dev_path) != split['manifest_sha256']['development']:
            raise ValueError('development manifest changed')
        all_development = json.loads(dev_path.read_text())['tasks']
        if len(all_development)!=development_count:raise ValueError('development manifest count mismatch')
        submitted = []
        machine_agreements = 0
        submission_attempts = 0
        ready = []

        def create_checkpoint():
            n = len(submitted)
            root = output/'checkpoints'/f'checkpoint_{n:02d}'
            entry = {'development_submission_count':n,'source_task_ids':[x['task']['task_id'] for x in submitted],
                     'source_assessments':{x['task']['task_id']:x['assessment']['status'] for x in submitted},
                     'status':'synthesis_running','held_results':[]}
            state['checkpoints'].append(entry); state['status']='checkpoint_synthesis_running'; checkpoint()
            try:
                examples = []
                for item in submitted:
                    if digest(item['path']) != item['sha256']:
                        raise ValueError('development script changed before synthesis')
                    examples.append({'task':item['task'],'script_sha256':item['sha256'],
                                     'assessment':{'status':item['assessment']['status'],
                                                   'reason':item['assessment'].get('reason','')},
                                     'trajectory':trajectory_excerpt(item['path'].parent),
                                     'script':item['path'].read_text()})
                result = synthesis_fn(url,contract,examples,root/'synthesis')
                candidate = root/'synthesis/candidate.py'
                if result.get('status') != 'candidate_ready' or digest(candidate) != result.get('candidate_sha256'):
                    entry['status']='synthesis_rejected'; checkpoint(); return
                entry['development_checks'] = []
                if verify_development:
                    for item in submitted:
                        if not within_budget():
                            entry['status']='stopped_budget'; checkpoint(); return
                        assessment = verify_case(url,contract,item['task'],candidate,
                                                 root/'development_checks'/item['task']['task_id'])
                        entry['development_checks'].append(assessment); checkpoint()
                    if any(x.get('status') != 'accepted' for x in entry['development_checks']):
                        entry['status']='candidate_not_verified_on_all_development_cases'; checkpoint(); return
                elif not webwright_only:
                    for item in submitted:
                        if not within_budget():
                            entry['status']='stopped_budget'; checkpoint(); return
                        assessment = safe_assessment(item['task'],candidate,
                                                     root/'development_checks'/item['task']['task_id'])
                        entry['development_checks'].append(assessment); checkpoint()
                    if sum(x.get('format_valid') is True for x in entry['development_checks']) < 2:
                        entry['status']='insufficient_format_valid_development'; checkpoint(); return
                frozen = root/'frozen.py'; shutil.copyfile(candidate,frozen)
                if digest(frozen) != result['candidate_sha256']:
                    raise ValueError('checkpoint hash mismatch')
                entry.update(status='frozen_before_any_held',sha256=digest(frozen),
                             frozen_at=datetime.now(timezone.utc).isoformat(),path=str(frozen))
                ready.append(entry); checkpoint()
            except Exception as exc:
                entry.update(status='checkpoint_error',error_type=type(exc).__name__,error_message=str(exc)[:300]); checkpoint()

        def run_batch(tasks, round_number):
            nonlocal machine_agreements, submission_attempts
            if not within_budget(): return
            state['status']='development_agents_running'; checkpoint()
            root = output/'development_rounds'/f'round_{round_number:02d}'
            root.parent.mkdir(parents=True,exist_ok=True)
            remaining = max(1, int(overall_seconds-(time.monotonic()-started)))
            batch_error = None
            try:
                development_fn(url,contract,tasks,root,
                    attempt_timeout=min(development.DEFAULT_ATTEMPT_TIMEOUT_SECONDS,remaining),
                    queue_timeout=min(len(tasks)*(development.DEFAULT_ATTEMPT_TIMEOUT_SECONDS
                        + development.BATCH_OVERHEAD_SECONDS_PER_TASK),remaining))
            except Exception as exc:
                batch_error = type(exc).__name__
            try:
                batch = json.loads((root/'summary.json').read_text())
                records = {x['task_id']:x for x in batch.get('attempts',[])}
            except (OSError, ValueError, TypeError):
                batch = {}
                records = {}
            state['rounds'].append({'round':round_number,'task_ids':[t['task_id'] for t in tasks],
                                    'batch_error_type':batch_error,'batch_status':batch.get('status')}); checkpoint()
            for task in tasks:
                # The budget may expire while a batch is running. Still record
                # attempts that actually finished; do not launch new work or
                # invent failures for tasks that were never started.
                expired = not within_budget()
                if expired and task['task_id'] not in records:
                    continue
                if batch.get('status')=='stopped_repeated_site_access_failures' and task['task_id'] not in records:
                    continue  # Do not call never-started tasks failed attempts.
                record = records.get(task['task_id'],{})
                path, origin = select_development_candidate(root,task,record)
                if expired:
                    assessment = {'task_id':task['task_id'],'status':'not_assessed_budget_exhausted',
                                  'reason':'Batch completed after budget; no further verification was launched.'}
                elif record.get('site_access_failure_signal'):
                    assessment = {'task_id':task['task_id'],'status':'not_assessable',
                                  'reason':'Repeated site-access failures; not a model-correctness verdict.'}
                elif verify_development and len(submitted) >= target:
                    assessment = {'task_id':task['task_id'],'status':'not_assessable',
                                  'reason':'Verification target already reached; not assessed.'}
                elif verify_development and origin == 'submitted' and path is not None:
                    assessment = verify_case(url,contract,task,path,output/'development_checks'/task['task_id'])
                elif verify_development:
                    assessment = {'task_id':task['task_id'],'status':'not_assessable',
                                  'reason':'WebWright did not submit a hash-matched script'}
                elif webwright_only and origin == 'submitted' and path is not None:
                    assessment = {'task_id':task['task_id'],'status':'webwright_submitted',
                                  'reason':'WebWright submitted after its configured self-reflection; not independently verified'}
                    if visual_verification:
                        # Old WebWright self-tests are not bound to the final script.
                        # Only an explicitly host-enabled isolated environment may replay it.
                        runner = getattr(environment, 'run_development_verification', None)
                        evidence = output/'development_verification_runs'/task['task_id']
                        expected = digest(path)
                        binding = None
                        if callable(runner):
                            try:
                                replay = runner(task,path,evidence)
                                copy = evidence/'workspace/candidate.py'
                                if (replay.get('process_exit') == 0 and copy.is_file() and not copy.is_symlink()
                                        and digest(path) == expected and digest(copy) == expected):
                                    binding = expected
                            except Exception as exc:
                                emit(output/'events.jsonl','development','verification_replay_error',
                                     task_id=task['task_id'],error_type=type(exc).__name__)
                        assessment = verify_saved(url,contract,task,evidence/'workspace/execution',
                            output/'visual_development'/task['task_id'],stage='development',
                            script_sha256=expected,evidence_script_sha256=binding)
                elif webwright_only:
                    assessment = {'task_id':task['task_id'],'status':'not_submitted',
                                  'reason':'WebWright did not submit a hash-matched script'}
                elif path is not None:
                    assessment = safe_assessment(task,path,output/'development_checks'/task['task_id'])
                else:
                    assessment = {'task_id':task['task_id'],'status':'not_assessable',
                                  'reason':'no submitted script or valid smoke checkpoint'}
                if verify_development and assessment.get('accepted_for_synthesis') is True:
                    try:
                        if digest(path) != assessment.get('script_sha256') or digest(path) != record.get('script_sha256'):
                            assessment.update(status='rejected', accepted_for_synthesis=False,
                                              reason='submitted script changed after verification')
                    except OSError:
                        assessment.update(status='inconclusive', accepted_for_synthesis=False,
                                          reason='submitted script unavailable after verification')
                state['development_attempts'].append({'round':round_number, 'trajectory_status':record.get('trajectory_status'),
                    'process_exit':record.get('process_exit'), 'candidate_origin':origin, **assessment}); checkpoint()
                emit(output/'events.jsonl','development','selection_recorded',task_id=task['task_id'],
                     round=round_number,candidate_origin=origin,selection_status=assessment['status'])
                if assessment['status']=='model_agreement':
                    machine_agreements += 1
                    state['machine_agreed_development_count']=machine_agreements; checkpoint()
                if origin == 'submitted' and path is not None:
                    submission_attempts += 1
                    state['webwright_submitted_attempt_count'] = submission_attempts
                    if verify_development or visual_verification:
                        state['submitted_development_count'] = submission_attempts
                eligible = not expired and (assessment.get('accepted_for_synthesis') is True if visual_verification else
                            not verify_development or assessment.get('accepted_for_synthesis') is True)
                if origin == 'submitted' and path is not None and eligible and len(submitted)<target:
                    if verify_development:
                        machine_agreements += 1
                        state['machine_agreed_development_count'] = machine_agreements
                    submitted.append({'task':task,'path':path,'sha256':digest(path),'assessment':assessment})
                    if verify_development or visual_verification:
                        state['accepted_development_count']=len(submitted)
                    else:
                        state['submitted_development_count']=len(submitted)
                    checkpoint()
                    if (not final_only and len(submitted)>=2) or (final_only and len(submitted)==target):
                        create_checkpoint()
            if batch.get('status')=='stopped_repeated_site_access_failures':
                state['site_access_failure_signal']=True
                checkpoint()

        run_batch(all_development,0)
        for round_number in range(1,max_rounds+1):
            if state.get('site_access_failure_signal') or len(submitted)>=target or not within_budget(): break
            state['status']='replacement_generation_running'; checkpoint()
            try:
                new = replacement_fn(url,contract,all_development,output/'tasks/reserved_fingerprints.json',
                    target-len(submitted),round_number,output/'replacements'/f'round_{round_number:02d}')
            except Exception as exc:
                state['rounds'].append({'round':round_number,'task_ids':[],
                    'reason':'replacement generation failed','error_type':type(exc).__name__}); checkpoint()
                break
            if not new:
                state['rounds'].append({'round':round_number,'task_ids':[],'reason':'no distinct valid replacements'}); checkpoint()
                continue
            all_development.extend(new)
            run_batch(new,round_number)
        state['development_attempt_count']=len(state['development_attempts'])
        state['machine_agreed_development_count']=machine_agreements
        state['submitted_development_count']=submission_attempts if (verify_development or visual_verification) else len(submitted)
        if state.get('site_access_failure_signal'):
            state.update(status='stopped_repeated_site_access_failures',held_opened=False); checkpoint(); return state
        if not ready:
            status = ('stopped_budget_before_synthesis' if not within_budget() else
                      'stopped_insufficient_accepted_development' if (verify_development or visual_verification) and len(submitted)<target else
                      'stopped_insufficient_submitted_trajectories' if final_only and len(submitted)<target else 'stopped_no_frozen_checkpoint')
            state.update(status=status,held_opened=False); checkpoint(); return state
        if not within_budget():
            state.update(status='stopped_budget_before_held',held_opened=False); checkpoint(); return state
        # All candidate generation has permanently ended before opening held inputs.
        state.update(status='all_checkpoints_frozen_before_held',generation_closed=True,
                     generation_closed_at=datetime.now(timezone.utc).isoformat()); checkpoint()
        held_path = output/'tasks/held_out/tasks.json'
        if digest(held_path) != split['manifest_sha256']['held_out']:
            raise ValueError('held-out manifest changed')
        held = json.loads(held_path.read_text())['tasks']
        if len(held)!=held_count: raise ValueError(f'expected {held_count} held-out tasks')
        state.update(status='held_checks_running',held_opened=True); checkpoint()
        for entry in ready:
            path = Path(entry['path'])
            for task in held:
                if not within_budget():
                    state.update(status='stopped_budget_during_held'); checkpoint(); return state
                if digest(path)!=entry['sha256']: raise ValueError('frozen checkpoint changed')
                check_path = output/'checkpoints'/f"checkpoint_{entry['development_submission_count']:02d}"/'held_checks'/task['task_id']
                if webwright_only:
                    try:
                        result = held_fn(task,path,check_path)
                        if visual_verification and result.get('process_exit') == 0:
                            archived = check_path/'workspace/candidate.py'
                            binding = digest(archived) if archived.is_file() and not archived.is_symlink() else None
                            result['visual_assessment'] = verify_saved(url,contract,task,
                                check_path/'workspace/execution',check_path/'visual_verification',
                                stage='held_out',script_sha256=entry['sha256'],evidence_script_sha256=binding)
                        elif visual_verification:
                            result['visual_assessment'] = {'status':'inconclusive',
                                'reason':'execution did not complete successfully'}
                    except Exception as exc:
                        result = {'task_id':task['task_id'],'status':'execution_error','semantic_verdict':None,
                                  'error_type':type(exc).__name__}
                else:
                    result = safe_assessment(task,path,check_path)
                entry['held_results'].append(result); checkpoint()
            if webwright_only:
                entry['status']='held_visual_assessed_experimental' if visual_verification else 'held_executed_ungraded'
            else:
                entry['held_model_agreements'] = sum(x['status']=='model_agreement' for x in entry['held_results'])
                entry['status']='held_assessed_exploratory'
            checkpoint()
        state.update(status=('complete_held_visual_experimental' if visual_verification else
                             'complete_held_outputs_ungraded' if webwright_only else 'complete_exploratory_checkpoint_comparison'),
                     finished_at=datetime.now(timezone.utc).isoformat()); checkpoint()
        return state
    except Exception as exc:
        state.update(status='stopped_error',error_type=type(exc).__name__,error_message=str(exc)[:500],
                     finished_at=datetime.now(timezone.utc).isoformat()); checkpoint(); raise
