"""Fail-closed blind-replication verifier, using one policy for every site.

The generated tool and an independently generated reference implementation
must return the same typed result (with a documented NBSP exception for ordinary
text) for both a visible default input and a held
input. Both executions must also be grounded on first-party live pages. This
certifies only the tested cases at verification time, not all possible inputs.
"""
from __future__ import annotations

import math
import re
from typing import Any

from generic_grounding import allowed_source, scalar_values


VERIFICATION_VERSION = 'blind_replication_nbsp_v2'
VERIFIED_STATUS = 'verified_blind_replication_v2'


def exact_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's bool/int equality shortcut."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(exact_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(exact_equal(a, b) for a, b in zip(left, right))
    return left == right


def comparison_mode(left: Any, right: Any, candidate: dict) -> str | None:
    """Permit only NBSP/space equivalence for declared ordinary text fields.

    This never changes saved outputs, parameters, URLs, identifiers, code,
    raw/exact-format tasks, object-valued fields, list order, or other bytes.
    """
    if exact_equal(left, right):
        return 'exact'
    contract = ' '.join(str(candidate.get(key, '')) for key in ('goal', 'boundary', 'success_contract'))
    if re.search(r'\b(?:exact|verbatim|raw|preformatted|whitespace|source code|byte[- ]for[- ]byte)\b',
                 contract, re.I):
        return None
    returns = candidate.get('returns')
    if not isinstance(left, dict) or not isinstance(right, dict) or not isinstance(returns, dict):
        return None
    if left.keys() != right.keys() or left.keys() != returns.keys():
        return None
    def canonical(value: Any, field: str, kind: Any) -> Any:
        if re.search(r'(?:url|uri|href|link|path|id|key|token|hash|code|raw)', field, re.I):
            return value
        declared = re.sub(r'\s+', '', str(kind).casefold())
        if declared in {'str', 'string'} and isinstance(value, str):
            return value.replace('\u00a0', ' ')
        if declared in {'list[str]', 'list[string]'} and isinstance(value, list):
            return [item.replace('\u00a0', ' ') if isinstance(item, str) else item for item in value]
        return value
    clean_left = {key: canonical(value, key, returns[key]) for key, value in left.items()}
    clean_right = {key: canonical(value, key, returns[key]) for key, value in right.items()}
    return 'nbsp_space_equivalent' if exact_equal(clean_left, clean_right) else None


def _nonempty_json_tree(value: Any, depth: int = 0) -> bool:
    """Reject empty or null nested fields when no optional schema is declared."""
    if depth > 10:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if type(value) is bool:
        return True
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, dict):
        return bool(value) and all(isinstance(key, str) and key.strip()
                                   and _nonempty_json_tree(item, depth + 1)
                                   for key, item in value.items())
    if isinstance(value, list):
        return bool(value) and all(_nonempty_json_tree(item, depth + 1) for item in value)
    return False


def required_quoted_item_fields(candidate: dict) -> set[str]:
    """Extract only an explicit `with 'a' and 'b' fields` contract."""
    returns = candidate.get('returns')
    if not isinstance(returns, dict) or sum(
        str(kind).casefold().replace(' ', '') in {'list[object]', 'list[dict]'}
        for kind in returns.values()) != 1:
        return set()
    text = str(candidate.get('success_contract', ''))
    match = re.search(r'''\b(?:with|containing)\s+((?:['"][a-z][a-z0-9_]*['"](?:\s*,\s*|\s+and\s+|\s+)?)+)\s+(?:fields|keys)\b''',
                      text, re.I)
    return set(re.findall(r'''['"]([a-z][a-z0-9_]*)['"]''', match.group(1), re.I)) if match else set()


def _matches_type(value: Any, declared: Any) -> bool:
    """Validate the small, explicit return-schema vocabulary fail closed."""
    kind = re.sub(r'\s+', '', str(declared).casefold())
    if kind in {'str', 'string', 'url'}:
        return isinstance(value, str) and bool(value.strip())
    if kind in {'int', 'integer'}:
        return type(value) is int
    if kind in {'float', 'number'}:
        return type(value) in {int, float} and math.isfinite(value)
    if kind in {'bool', 'boolean'}:
        return type(value) is bool
    if kind in {'dict', 'object', 'map'}:
        return isinstance(value, dict) and _nonempty_json_tree(value)
    if kind == 'list' or kind.startswith('list[') or kind.startswith('array['):
        if not isinstance(value, list) or not value:
            return False
        if kind in {'list[object]', 'list[dict]', 'array[object]', 'array[dict]'}:
            return (all(isinstance(item, dict) and _nonempty_json_tree(item) for item in value)
                    and all(item.keys() == value[0].keys() for item in value))
        if kind in {'list[str]', 'list[string]', 'array[str]', 'array[string]'}:
            return all(isinstance(item, str) and bool(item.strip()) for item in value)
        # Unknown element types are not silently accepted.
        return kind == 'list' and _nonempty_json_tree(value)
    return False


def _validate_output(website: str, label: str, output: Any, case: dict,
                     returns: dict, actor: str, errors: list[str], candidate: dict | None = None) -> Any:
    if not isinstance(output, dict):
        errors.append(f'{label}: missing {actor} output')
        return None
    if not exact_equal(output.get('parameters'), case):
        errors.append(f'{label}: {actor} parameters do not match requested input')
    source = output.get('source_url')
    if not isinstance(source, str) or not allowed_source(website, source):
        errors.append(f'{label}: {actor} source_url is not first-party HTTPS')
    result = output.get('result')
    if not isinstance(result, dict) or result.keys() != returns.keys():
        errors.append(f'{label}: {actor} result fields do not exactly match declared schema')
        return result
    for field, declared in returns.items():
        if not _matches_type(result[field], declared):
            errors.append(f'{label}: {actor} field {field} does not match type {declared}')
        elif (candidate is not None and str(declared).casefold().replace(' ', '') in {'list[object]', 'list[dict]'}):
            required = required_quoted_item_fields(candidate)
            if required and any(not required <= item.keys() for item in result[field]):
                errors.append(f'{label}: {actor} field {field} lacks explicitly required item fields')
    # A narrowly explicit exact-link-text contract with identically named
    # string input and output cannot be satisfied by a substring match.
    if (candidate is not None and 'link_text' in case and 'link_text' in result
            and isinstance(case['link_text'], str) and isinstance(result['link_text'], str)
            and re.search(r'\bexact\s+visible\s+link\s+text\b',
                          str(candidate.get('success_contract', '')), re.I)
            and result['link_text'] != case['link_text']):
        errors.append(f'{label}: {actor} exact link_text does not match requested link_text')
    try:
        if not scalar_values(result):
            errors.append(f'{label}: {actor} result has no nonempty scalar values')
    except ValueError:
        errors.append(f'{label}: {actor} result exceeds structural limits')
    return result


def validate_tool_output(website: str, candidate: dict, label: str,
                         output: Any, case: dict) -> list[str]:
    """Validate one generated-tool output before spending on a reference agent."""
    errors: list[str] = []
    returns = candidate.get('returns')
    if not isinstance(returns, dict) or not returns:
        return ['candidate must declare a nonempty return schema']
    _validate_output(website, label, output, case, returns, 'tool', errors, candidate)
    return errors


def assess(website: str, candidate: dict, cases: dict, outputs: dict,
           reference: dict, grounding: dict, reference_grounding: dict) -> dict:
    """Accept typed blind replication with explicit comparison modes."""
    errors: list[str] = []
    if not {'default', 'held'} <= set(cases) or set(cases) - {'default', 'held', 'stress_boundary', 'secondary_held'}:
        return {'status': 'fail', 'errors': ['default and held cases are required'],
                'counts_as_verified': False, 'verification_version': VERIFICATION_VERSION}
    if exact_equal(cases['default'], cases['held']):
        errors.append('default and held inputs must differ')
    returns = candidate.get('returns')
    if not isinstance(returns, dict) or not returns:
        errors.append('candidate must declare a nonempty return schema')
        returns = {}
    tool_results: dict[str, Any] = {}
    reference_results: dict[str, Any] = {}
    comparison_modes: dict[str, str] = {}
    for label in cases:
        tool_results[label] = _validate_output(
            website, label, outputs.get(label), cases[label], returns, 'tool', errors, candidate)
        reference_results[label] = _validate_output(
            website, label, reference.get(label), cases[label], returns, 'reference', errors, candidate)
        mode = comparison_mode(tool_results[label], reference_results[label], candidate)
        if mode is None:
            errors.append(f'{label}: tool output differs from blind reference')
        else:
            comparison_modes[label] = mode
        for actor, report in (('tool', grounding), ('reference', reference_grounding)):
            if report.get('cases', {}).get(label, {}).get('status') != 'values_found':
                errors.append(f'{label}: {actor} values are not grounded in an independent live page')
    if comparison_mode(tool_results.get('default'), tool_results.get('held'), candidate) is not None:
        errors.append('tool result did not change when the input changed')
    if comparison_mode(reference_results.get('default'), reference_results.get('held'), candidate) is not None:
        errors.append('reference result did not change when the input changed')
    return {
        'status': 'fail' if errors else VERIFIED_STATUS,
        'errors': errors,
        'counts_as_verified': not errors,
        'verification_version': VERIFICATION_VERSION,
        'scope': 'Independent replication for the recorded cases only: ' + ', '.join(cases),
        'comparison_modes': comparison_modes,
        'visible_development_cases': [key for key in ('default', 'stress_boundary') if key in cases],
        'held_out_cases': [key for key in ('held', 'secondary_held') if key in cases],
        'limitation': ('Ordinary text can be NBSP/space-equivalent rather than byte-exact; '
                       'finite blind replication does not prove correctness for every input, '
                       'and independently generated implementations can share a mistake.'),
    }
