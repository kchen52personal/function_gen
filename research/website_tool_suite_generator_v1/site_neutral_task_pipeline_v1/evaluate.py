"""Site-neutral execution and blind-reference assessment; no site adapters."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

PROJECT = Path('/shared/hans/research/website_tool_suite_generator_v1')
PIPELINE = PROJECT / 'pipeline_codex_v73'
sys.path.insert(0, str(PIPELINE))
from codex_bounded_process import run_bounded
from codex_artifact_guard import checked_file
from codex_screenshots import ensure_screenshots
from generic_grounding import check as grounding
from result_log import read_result
from uniform_reference_agent import run as blind_reference
from uniform_reference_check import validate_tool_output
from factory import cli_value

SANDBOX = PIPELINE / 'sandbox_launch.py'
DEFAULT_REFERENCE_MODEL = 'qwen3.8-flash'

def run_logged(cmd, *, cwd, env, timeout, prefix):
    code, stdout, stderr = run_bounded(cmd, cwd=cwd, env=env, timeout=timeout)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix('.stdout.txt').write_text(stdout, encoding='utf-8')
    prefix.with_suffix('.stderr.txt').write_text(stderr, encoding='utf-8')
    return code

def run_ungraded_case(task, script, output):
    """Run frozen code on one case; record execution, not correctness."""
    output.mkdir(parents=True, exist_ok=False)
    workspace = output/'workspace'
    workspace.mkdir()
    copy = workspace/'final_script.py'
    if not script.is_file() or script.is_symlink():
        raise ValueError('script missing or unsafe')
    shutil.copyfile(script, copy)
    params = task['parameters']
    flags = [part for key, value in params.items() for part in (f'--{key}', cli_value(value))]
    env = {**os.environ, 'WORKSPACE_DIR':str(workspace),
           'PLAYWRIGHT_BROWSERS_PATH':str(PROJECT/'.playwright-browsers')}
    code = run_logged([str(SANDBOX), sys.executable, str(copy), *flags, '--output-dir', str(workspace/'execution')],
                      cwd=workspace, env=env, timeout=150, prefix=output/'execution')
    result = {'task_id':task['task_id'], 'status':'ran_ungraded' if code == 0 else 'execution_failed',
              'process_exit':code, 'semantic_verdict':None,
              'reason':'No correctness verifier was used; a zero exit is not a pass.'}
    (output/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return result

def evaluate_case(url, contract, task, script, output, reference_model=DEFAULT_REFERENCE_MODEL, reference_calls=12):
    output.mkdir(parents=True, exist_ok=False)
    result = {'task_id':task['task_id'], 'status':'inconclusive', 'reason':'not evaluated',
              'automated_assessment':True, 'independent_human_review':False, 'format_valid':False,
              'limitations':['Value grounding and blind model agreement do not prove semantic correctness.',
                             'Live website data and ranking can change between independent runs.']}
    def save():
        (output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return result
    if not script.is_file() or script.is_symlink():
        result['reason'] = 'script is missing or unsafe'
        return save()
    workspace = output / 'workspace'
    workspace.mkdir()
    copy = workspace / 'final_script.py'
    shutil.copyfile(script, copy)
    source_hash = hashlib.sha256(copy.read_bytes()).hexdigest()
    params = task['parameters']
    flags = [part for key, value in params.items() for part in (f'--{key}', cli_value(value))]
    run_dir = workspace / 'execution'
    env = {**os.environ, 'WORKSPACE_DIR':str(workspace),
           'PYTHONPATH':f'{PIPELINE}:{PROJECT / "vendor/Webwright/src"}',
           'PLAYWRIGHT_BROWSERS_PATH':str(PROJECT / '.playwright-browsers'),
           'CODEX_INDEPENDENT_REFERENCE_RECON':'1'}
    code = run_logged([str(SANDBOX), sys.executable, str(copy), *flags, '--output-dir', str(run_dir)],
                      cwd=workspace, env=env, timeout=150, prefix=output/'execution')
    if code in (124, 125, 126):
        result.update(status='inconclusive', reason=f'clean execution was interrupted or exceeded a limit ({code})')
        return save()
    if code != 0:
        result.update(status='fail', reason=f'clean execution exited {code}')
        return save()
    try:
        value = read_result(checked_file(workspace, run_dir / 'final_script_log.txt'))
        errors = validate_tool_output(url, contract, 'development', value, params)
        errors.extend(ensure_screenshots(workspace, run_dir))
        if hashlib.sha256(copy.read_bytes()).hexdigest() != source_hash:
            errors.append('script changed during execution')
        if errors:
            result.update(status='fail', reason='; '.join(errors))
            return save()
    except (OSError, ValueError, TypeError) as exc:
        result.update(status='fail', reason='missing or invalid result: ' + type(exc).__name__)
        return save()
    result['format_valid'] = True  # Successful fresh execution + schema/artifact checks; not correctness.
    report = asyncio.run(grounding(url, {'default':value}))
    (output / 'grounding.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if report.get('cases', {}).get('default', {}).get('status') != 'values_found':
        result['reason'] = 'fresh first-party value grounding was unavailable or incomplete'
        default_grounding = report.get('cases', {}).get('default', {})
        result['grounding_status'] = default_grounding.get('status')
        result['missing_value_paths'] = default_grounding.get('missing_value_paths', [])[:20]
        return save()
    # The blind reference needs the specific request as well as the general
    # contract and inputs. It still receives neither the candidate output nor
    # code in its model prompt; the comparator receives them separately.
    candidate = {**contract, 'name':'site_function'}
    if isinstance(task.get('intent'), str) and task['intent'].strip():
        candidate['goal'] = (contract['goal'] + '\nSpecific request: ' + task['intent'])[:6000]
    try:
        reference = blind_reference(url, candidate, {'default':params}, {'default':value}, report,
            output / 'blind_reference', env, reference_calls, run_logged,
            model_name=reference_model, visible_only=True)
    except Exception as exc:
        result['reason'] = 'blind reference unavailable: ' + type(exc).__name__
        return save()
    (output / 'reference_summary.json').write_text(json.dumps(reference, ensure_ascii=False, indent=2) + '\n')
    reference_status = reference.get('status')
    if reference_status == 'visible_reference_agreement':
        result.update(status='model_agreement', reason='typed output matched a blind reference on this case',
                      script_sha256=source_hash)
    elif reference_status == 'visible_reference_mismatch':
        result.update(status='inconclusive', reason='blind reference disagreed; either implementation may be wrong')
    else:
        result['reason'] = 'blind reference did not establish agreement: ' + str(reference_status)
        result['reference_reason'] = str(reference.get('reason', ''))[:500]
    return save()
