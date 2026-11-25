"""Prepare conservative scalar test inputs independently of the code writer.

Examples are synthesized from candidate defaults and simple constraints. They
are not proof that an input is valid on the live site or that output is correct.
If a safe differing example cannot be inferred, fail rather than invent one.
"""
from __future__ import annotations

import math
import re
from typing import Any


def invalid_integer_cases(candidate: dict, cases: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Probe both sides of one explicit inclusive integer range; never guess bounds."""
    default = cases.get('default')
    if not isinstance(default, dict):
        return {}
    for spec in candidate.get('arguments', []):
        if not isinstance(spec, dict) or str(spec.get('type', '')).lower() not in {'int', 'integer'}:
            continue
        match = re.search(r'\bbetween\s+(\d+)\s+and\s+(\d+)\s+inclusive\b',
                          str(spec.get('constraints', '')), re.I)
        name = spec.get('name')
        if not match or name not in default:
            continue
        low, high = int(match.group(1)), int(match.group(2))
        if low > high or high > 100:
            continue
        return {'below_minimum': {**default, name: low - 1},
                'above_maximum': {**default, name: high + 1}}
    return {}


def add_boundary_case(candidate: dict, cases: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Add bounded integer or small enum probes; the first extra is disclosed."""
    expanded = dict(cases)
    default, held = cases.get('default'), cases.get('held')
    if not isinstance(default, dict) or not isinstance(held, dict):
        return expanded
    for spec in candidate.get('arguments', []):
        if not isinstance(spec, dict) or str(spec.get('type', '')).lower() not in {'int', 'integer'}:
            continue
        match = re.search(r'\bbetween\s+(\d+)\s+and\s+(\d+)\s+inclusive\b',
                          str(spec.get('constraints', '')), re.I)
        name = spec.get('name')
        if not match or name not in default:
            continue
        low, high = int(match.group(1)), int(match.group(2))
        if low > high or high > 100:
            continue
        for value in (high, low):
            alternative = {**default, name: value}
            if alternative != default and alternative != held:
                expanded['stress_boundary'] = alternative
                # The boundary is disclosed during development; keep an
                # additional distinct interior case withheld where possible.
                for interior in (high - 1, low + 1, low + (high - low) // 2):
                    hidden = {**default, name: interior}
                    if low <= interior <= high and hidden not in (default, held, alternative):
                        expanded['secondary_held'] = hidden
                        break
                return expanded
    # Cover up to four values of a single explicit enum. For a larger enum,
    # prioritize a shorter label contained in another label as a withheld
    # ambiguity probe. The first extra is disclosed; this is not full coverage
    # for domains larger than four or combinations of arguments.
    arguments = candidate.get('arguments')
    if not isinstance(arguments, list) or len(arguments) != 1 or not isinstance(arguments[0], dict):
        return expanded
    spec = arguments[0]
    if str(spec.get('type', '')).lower() not in {'str', 'string'}:
        return expanded
    name = spec.get('name')
    match = re.fullmatch(r'\s*(?:must\s+be\s+)?one\s+of\s*:?\s*([\w -]+(?:\s*,\s*[\w -]+){2,11})\s*',
                         str(spec.get('constraints', '')), re.I)
    if not match or name not in default or name not in held:
        return expanded
    options = [value.strip() for value in match.group(1).split(',')]
    if len(set(options)) != len(options) or default[name] not in options or held[name] not in options:
        return expanded
    remaining = [value for value in options if value not in {default[name], held[name]}]
    if len(options) > 4:
        ambiguous = next((value for value in remaining if any(
            value.casefold() != other.casefold() and value.casefold() in other.casefold()
            for other in options)), None)
        if ambiguous and len(remaining) > 1:
            remaining = [next(value for value in remaining if value != ambiguous), ambiguous]
    if remaining:
        expanded['stress_boundary'] = {**default, name: remaining[0]}
    if len(remaining) > 1:
        expanded['secondary_held'] = {**default, name: remaining[1]}
    return expanded


def _number_bounds(text: str) -> tuple[float | None, float | None]:
    text = text.lower()
    low = 1.0 if re.search(r'\bpositive\b', text) else None
    if re.search(r'\bnon[- ]negative\b', text):
        low = 0.0
    high = None
    between = re.search(r'between\s+(-?\d+(?:\.\d+)?)\s+(?:and|to)\s+(-?\d+(?:\.\d+)?)', text)
    if between:
        low, high = float(between.group(1)), float(between.group(2))
    return low, high


def prepare(candidate: dict) -> dict[str, dict[str, Any]]:
    arguments = candidate.get('arguments')
    if not isinstance(arguments, list):
        raise ValueError('argument definitions must be a list')
    # No-argument functionalities can only be replayed in two fresh runs;
    # this is not a distinct-input generalization test.
    if not arguments:
        return {'default': {}, 'held': {}}
    explicit = candidate.get('test_cases')
    if explicit is not None:
        if not isinstance(explicit, dict) or set(explicit) != {'default', 'held'}:
            raise ValueError('test_cases must contain exactly default and held')
        names = {spec.get('name') for spec in arguments if isinstance(spec, dict)}
        if None in names or len(names) != len(arguments):
            raise ValueError('invalid or duplicate argument name')
        normalized: dict[str, dict[str, Any]] = {}
        for label in ('default', 'held'):
            case = explicit[label]
            if not isinstance(case, dict) or set(case) != names:
                raise ValueError(f'test_cases.{label} must contain every argument and no others')
            normalized[label] = {}
            for spec in arguments:
                name = spec['name']
                kind = str(spec.get('type', '')).lower().strip()
                value = case[name]
                if kind in {'str', 'string'}:
                    if not isinstance(value, str) or not value.strip() or len(value) > 500:
                        raise ValueError(f'test_cases.{label}.{name}: invalid string')
                elif kind in {'int', 'integer'}:
                    if type(value) is not int:
                        raise ValueError(f'test_cases.{label}.{name}: invalid integer')
                elif kind in {'float', 'number'}:
                    if type(value) not in {int, float} or not math.isfinite(value):
                        raise ValueError(f'test_cases.{label}.{name}: invalid number')
                elif kind in {'bool', 'boolean'}:
                    if type(value) is not bool:
                        raise ValueError(f'test_cases.{label}.{name}: invalid boolean')
                else:
                    raise ValueError(f'{name}: unsupported argument type')
                normalized[label][name] = value
        defaults = {spec['name']: spec.get('default') for spec in arguments}
        if normalized['default'] != defaults:
            raise ValueError('test_cases.default must exactly match argument defaults')
        if normalized['default'] == normalized['held']:
            raise ValueError('test_cases.held must differ from default')
        return normalized
    default: dict[str, Any] = {}
    alternates: list[tuple[str, Any]] = []
    for spec in arguments:
        if not isinstance(spec, dict):
            raise ValueError('argument definition is not an object')
        name = spec.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name) or name in default:
            raise ValueError('invalid or duplicate argument name')
        kind = str(spec.get('type', '')).lower().strip()
        raw = spec.get('default')
        if raw is None:
            raise ValueError(f'{name}: no usable default input')
        constraints = str(spec.get('constraints', ''))
        if kind in {'int', 'integer', 'float', 'number'}:
            if type(raw) is bool:
                raise ValueError(f'{name}: boolean is not a number')
            try:
                value = int(raw) if kind in {'int', 'integer'} else float(raw)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError(f'{name}: invalid numeric default') from error
            if not math.isfinite(value) or abs(value) > 1_000_000:
                raise ValueError(f'{name}: numeric default outside safety bounds')
            low, high = _number_bounds(constraints)
            if (low is not None and value < low) or (high is not None and value > high):
                raise ValueError(f'{name}: numeric default outside declared bounds')
            default[name] = value
            # Without an explicit range/positivity, do not guess a valid number.
            if low is not None or high is not None:
                for alt in (value - 1, value + 1):
                    if (low is None or alt >= low) and (high is None or alt <= high):
                        alternates.append((name, alt))
                        break
        elif kind in {'bool', 'boolean'}:
            if type(raw) is bool:
                value = raw
            elif isinstance(raw, str) and raw.lower() in {'true', 'false'}:
                value = raw.lower() == 'true'
            else:
                raise ValueError(f'{name}: invalid boolean default')
            default[name] = value
            if not re.search(r'\b(?:only|must|always)\s+(?:be\s+)?(?:true|false)\b', constraints, re.I):
                alternates.append((name, not value))
        elif kind in {'str', 'string'}:
            if not isinstance(raw, str) or not raw.strip() or len(raw) > 500:
                raise ValueError(f'{name}: invalid string default')
            default[name] = raw
            match = re.search(r'\bone of\s*:\s*([\w ,|/-]+)', constraints, re.I)
            if match:
                choices = [x.strip() for x in re.split(r'[,|]', match.group(1)) if x.strip()]
                alt = next((x for x in choices if x.casefold() != raw.casefold()), None)
                if alt is not None:
                    alternates.append((name, alt))
        else:
            raise ValueError(f'{name}: unsupported argument type')
    if not alternates:
        raise ValueError('no safely inferable distinct held input')
    held = dict(default)
    key, value = alternates[0]
    held[key] = value
    return {'default': default, 'held': held}
