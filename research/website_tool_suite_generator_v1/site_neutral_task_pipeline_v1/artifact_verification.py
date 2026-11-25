"""Conservative visual assessment of saved browser runs, without executing them.

This experimental component is not a calibrated correctness oracle. The caller
must supply the specific execution directory; no discovery of held data occurs.
"""
import hashlib
import json
from pathlib import Path
import re

import visual_verifier
from codex_artifact_guard import checked_file


def verify_saved(site, contract, task, execution_dir, output, *, stage,
                 script_sha256, evidence_script_sha256=None, judge=None,
                 model='qwen3.5-flash', timeout_seconds=120):
    """Assess exactly one saved run; missing or unbound evidence is inconclusive.

    evidence_script_sha256 must come from trusted harness instrumentation, never
    the browser agent or a user-provided claim. Legacy runs generally lack it.
    """
    if stage not in {'development', 'held_out'}:
        raise ValueError('invalid verification stage')
    root = Path(execution_dir)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {'task_id': task.get('task_id'), 'stage': stage,
              'status': 'inconclusive', 'accepted_for_synthesis': False,
              'eligible_for_pipeline_gate': False,
              'is_ground_truth': False, 'script_sha256': script_sha256,
              'reason': 'evidence unavailable',
              'limitations': ['Visual assessments are experimental and may be wrong.',
                  'Screenshots may omit important earlier or offscreen events.',
                  'Generated scripts can write or replace their own screenshots and log.',
                  'Script hash binding does not establish independent evidence provenance.',
                  'A verified observation is not a security guarantee.']}
    def save():
        (output/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        return report
    try:
        if (not isinstance(script_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', script_sha256)
                or evidence_script_sha256 != script_sha256):
            report['reason'] = 'execution evidence is not bound to the submitted or frozen script hash'
            return save()
        log = checked_file(root, root/'final_script_log.txt')
        after = checked_file(root, root/'screenshots/after.png')
        images = []
        roles = []
        try:
            images.append(checked_file(root, root/'screenshots/before.png'))
            roles.append('before')
        except (OSError, ValueError):
            pass
        images.append(after)
        roles.append('after')
        text = log.read_text(encoding='utf-8')
        result, legacy = visual_verifier.parse_result_log(text)
        report['log_sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        report['evidence_script_sha256'] = evidence_script_sha256
        judge = judge or visual_verifier.judge
        assessment = judge(site, contract, task, result, images, output/'visual',
                           legacy_marker=legacy, model=model, timeout_seconds=timeout_seconds, image_roles=roles)
        report['assessment'] = assessment
        semantic = assessment.get('status')
        fmt = assessment.get('format', {}).get('status')
        if semantic == 'fail' or fmt == 'fail':
            report.update(diagnostic_status='fail', reason='visual or output-contract assessment failed, '
                          'but script-authored evidence is not independently authenticated')
        elif semantic == 'pass' and fmt == 'pass':
            report.update(diagnostic_status='pass', reason='experimental visual and format assessments passed; '
                          'script-authored evidence cannot establish independent acceptance')
        else:
            report['diagnostic_status']='inconclusive'
            report['reason'] = 'visual or format assessment inconclusive'
    except Exception as exc:
        report.update(reason='verification unavailable: '+type(exc).__name__, error_type=type(exc).__name__)
    return save()
