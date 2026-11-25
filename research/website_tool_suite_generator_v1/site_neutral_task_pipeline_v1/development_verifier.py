"""Fail-closed development gate over the existing independent assessment.

Acceptance is evidence, not proof. This module does not inspect held-out inputs.
"""
import hashlib
import json
from pathlib import Path

from evaluate import evaluate_case


def verify_case(url, contract, task, script, output, evaluator=None):
    evaluator = evaluator or evaluate_case
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    result = {'task_id': task['task_id'], 'status': 'inconclusive',
              'reason': 'verification did not finish', 'accepted_for_synthesis': False,
              'is_ground_truth': False,
              'limitations': ['Independent reference and original agent may share mistakes.',
                  'Values on a page do not prove relevance, completeness, or correct ordering.',
                  'Live data can change and exact comparison can reject correct answers.']}
    try:
        script = Path(script)
        if script.is_symlink() or not script.is_file():
            raise ValueError('missing or unsafe submitted script')
        before = hashlib.sha256(script.read_bytes()).hexdigest()
        assessment = evaluator(url, contract, task, script, output/'assessment')
        after = hashlib.sha256(script.read_bytes()).hexdigest()
        result['script_sha256'] = before
        result['assessment'] = assessment
        if after != before:
            result.update(status='rejected', reason='submitted script changed during verification')
        elif assessment.get('status') == 'model_agreement' and assessment.get('format_valid') is True:
            result.update(status='accepted', accepted_for_synthesis=True,
                          reason='fresh execution, schema, grounding and blind reference agreed')
        elif assessment.get('status') == 'fail':
            result.update(status='rejected', reason=str(assessment.get('reason','assessment failed'))[:500])
        else:
            result.update(reason=str(assessment.get('reason','assessment inconclusive'))[:500])
    except Exception as exc:
        result.update(reason='verification unavailable: '+type(exc).__name__, error_type=type(exc).__name__)
    (output/'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return result
