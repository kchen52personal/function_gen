#!/usr/bin/env python3
"""Run a previously verified generated tool, without a model or API key.

Verification is evidence for tested inputs and time, not a guarantee that a
live website has not changed. Runs are isolated from the saved tool/evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4

from result_log import read_result
from uniform_reference_check import VERIFICATION_VERSION, VERIFIED_STATUS, validate_tool_output
from codex_artifact_guard import checked_file
from codex_bounded_process import run_bounded
from select_candidates import goal_schema_mismatches, integer_range_coverage_errors
from generic_grounding import explicit_aria_fields

ROOT = Path('/shared/hans/research/website_tool_suite_generator_v1').resolve()
SANDBOX = ROOT / 'pipeline/sandbox_launch.py'
REQUIRED_POLICIES = {'codex_v13_artifact_normalized_tte', 'codex_v14_resumable_suite_tte',
                     'codex_v15_explicit_reference_tte', 'codex_v16_safe_invocation_tte',
                     'codex_v17_robust_dom_tte', 'codex_v18_form_evidence_tte',
                     'codex_v19_clean_list_contract_tte', 'codex_v20_invalid_input_tte',
                     'codex_v21_visible_edge_tte', 'codex_v22_grounded_visible_edge_tte',
                     'codex_v23_nbsp_equivalence_tte', 'codex_v24_dom_id_grounding_tte',
                     'codex_v25_numeric_grounding_tte', 'codex_v26_numeric_node_grounding_tte',
                     'codex_v27_proposal_fallback_tte', 'codex_v28_structural_content_tte',
                     'codex_v29_list_advisory_tte', 'codex_v30_parameter_echo_tte',
                     'codex_v31_cross_family_generator_tte', 'codex_v32_bounded_output_tte',
                     'codex_v33_nested_output_tte', 'codex_v34_healthy_reference_tte',
                     'codex_v35_compact_resume_tte', 'codex_v36_honest_failure_usage_tte',
                     'codex_v37_goal_schema_tte', 'codex_v38_integer_range_coverage_tte',
                     'codex_v39_contract_dedup_tte', 'codex_v40_goal_link_schema_tte',
                     'codex_v41_visible_disagreement_evolution_tte', 'codex_v42_attributed_visible_evolution_tte',
                     'codex_v43_precise_sample_tte', 'codex_v44_live_value_guidance_tte',
                     'codex_v45_atomic_checkpoints_tte', 'codex_v46_precise_headline_tte',
                     'codex_v47_visible_value_feedback_tte', 'codex_v48_recon_bucket_guard_tte',
                     'codex_v49_nested_structure_tte', 'codex_v50_missing_list_tte',
                     'codex_v51_internal_evidence_guard_tte', 'codex_v52_bounded_proposal_response_tte',
                     'codex_v53_spend_logging_tte', 'codex_v54_field_type_guard_tte',
                     'codex_v55_partial_catalog_tte', 'codex_v56_section_context_tte',
                     'codex_v57_ancestor_context_tte', 'codex_v58_independent_recon_tte',
                     'codex_v59_return_context_tte', 'codex_v60_reference_inspection_tte',
                     'codex_v61_headless_reference_tte', 'codex_v62_observed_domain_guard_tte',
                     'codex_v63_optional_independent_recon_tte'}
REQUIRED_POLICIES.add('codex_v64_indexed_list_overlap_tte')
REQUIRED_POLICIES.add('codex_v65_position_index_overlap_tte')
REQUIRED_POLICIES.add('codex_v66_enum_label_overlap_tte')
REQUIRED_POLICIES.add('codex_v67_relative_link_grounding_tte')
REQUIRED_POLICIES.add('codex_v68_small_enum_coverage_tte')
REQUIRED_POLICIES.add('codex_v69_aria_attribute_grounding_tte')
REQUIRED_POLICIES.add('codex_v70_named_page_dedup_tte')
REQUIRED_POLICIES.add('codex_v71_labelled_recon_tte')
REQUIRED_POLICIES.add('codex_v72_visible_controls_tte')
REQUIRED_POLICIES.add('codex_v73_user_goal_focus_tte')


def inside_project(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if ROOT not in resolved.parents:
        raise ValueError('path is outside the website-tool project')
    return resolved


def select_tool(summary_path: Path, name: str) -> dict:
    summary = json.loads(inside_project(summary_path).read_text(encoding='utf-8'))
    if summary.get('pipeline_policy') not in REQUIRED_POLICIES:
        raise ValueError('summary does not use the current blind-replication policy')
    matches = [entry for entry in summary.get('accepted', [])
               if entry.get('candidate', {}).get('name') == name
               and entry.get('status') == 'verified' and entry.get('counted') is True]
    if len(matches) != 1:
        raise ValueError('expected exactly one verified tool with that name')
    entry = matches[0]
    if (explicit_aria_fields(entry.get('candidate'))
            and summary.get('pipeline_policy') not in {'codex_v69_aria_attribute_grounding_tte',
                                                       'codex_v70_named_page_dedup_tte',
                                                       'codex_v71_labelled_recon_tte',
                                                       'codex_v72_visible_controls_tte',
                                                       'codex_v73_user_goal_focus_tte'}):
        raise ValueError('older ARIA-field certificate lacks attribute-specific grounding; reverify live')
    mismatches = goal_schema_mismatches(entry['candidate'])
    if mismatches:
        raise ValueError('tool contract does not cover its stated goal or input constraints: ' + '; '.join(mismatches))
    range_errors = integer_range_coverage_errors(entry['candidate'])
    if range_errors:
        raise ValueError('tool lacks supported boundary coverage: ' + '; '.join(range_errors))
    report_path = inside_project(Path(entry.get('verification', '')))
    report = json.loads(report_path.read_text(encoding='utf-8'))
    expected = ((VERIFIED_STATUS, VERIFICATION_VERSION)
                if summary.get('pipeline_policy') in {'codex_v23_nbsp_equivalence_tte', 'codex_v24_dom_id_grounding_tte', 'codex_v25_numeric_grounding_tte', 'codex_v26_numeric_node_grounding_tte', 'codex_v27_proposal_fallback_tte', 'codex_v28_structural_content_tte', 'codex_v29_list_advisory_tte', 'codex_v30_parameter_echo_tte', 'codex_v31_cross_family_generator_tte', 'codex_v32_bounded_output_tte', 'codex_v33_nested_output_tte', 'codex_v34_healthy_reference_tte', 'codex_v35_compact_resume_tte', 'codex_v36_honest_failure_usage_tte', 'codex_v37_goal_schema_tte', 'codex_v38_integer_range_coverage_tte', 'codex_v39_contract_dedup_tte', 'codex_v40_goal_link_schema_tte', 'codex_v41_visible_disagreement_evolution_tte', 'codex_v42_attributed_visible_evolution_tte', 'codex_v43_precise_sample_tte', 'codex_v44_live_value_guidance_tte', 'codex_v45_atomic_checkpoints_tte', 'codex_v46_precise_headline_tte', 'codex_v47_visible_value_feedback_tte', 'codex_v48_recon_bucket_guard_tte', 'codex_v49_nested_structure_tte', 'codex_v50_missing_list_tte', 'codex_v51_internal_evidence_guard_tte', 'codex_v52_bounded_proposal_response_tte', 'codex_v53_spend_logging_tte', 'codex_v54_field_type_guard_tte', 'codex_v55_partial_catalog_tte', 'codex_v56_section_context_tte', 'codex_v57_ancestor_context_tte', 'codex_v58_independent_recon_tte', 'codex_v59_return_context_tte', 'codex_v60_reference_inspection_tte', 'codex_v61_headless_reference_tte', 'codex_v62_observed_domain_guard_tte', 'codex_v63_optional_independent_recon_tte'}
                else ('verified_blind_replication_v1', 'blind_replication_v1'))
    if summary.get('pipeline_policy') in {'codex_v64_indexed_list_overlap_tte',
                                          'codex_v65_position_index_overlap_tte',
                                          'codex_v66_enum_label_overlap_tte',
                                          'codex_v67_relative_link_grounding_tte',
                                          'codex_v68_small_enum_coverage_tte',
                                          'codex_v69_aria_attribute_grounding_tte',
                                          'codex_v70_named_page_dedup_tte',
                                          'codex_v71_labelled_recon_tte',
                                          'codex_v72_visible_controls_tte',
                                          'codex_v73_user_goal_focus_tte'}:
        expected = (VERIFIED_STATUS, VERIFICATION_VERSION)
    if (report.get('status') != expected[0]
            or report.get('counts_as_verified') is not True
            or report.get('verification_version') != expected[1]):
        raise ValueError('tool lacks a current blind-replication certificate')
    script = inside_project(Path(entry['script']))
    if hashlib.sha256(script.read_bytes()).hexdigest() != entry.get('script_sha256'):
        raise ValueError('verified script has changed')
    return entry


def prepare_arguments(entry: dict, supplied: dict) -> dict:
    if not isinstance(supplied, dict):
        raise ValueError('arguments must be a JSON object')
    contract = entry['candidate'].get('arguments', [])
    specs = {x['name']: x for x in contract}
    if any(not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', key) for key in specs):
        raise ValueError('invalid argument name in verified contract')
    if set(supplied) - set(specs):
        raise ValueError('argument not in verified contract')
    values = dict(supplied)
    for key, spec in specs.items():
        if key not in values:
            if spec.get('default') is None:
                raise ValueError(f'missing argument: {key}')
            values[key] = spec['default']
    for key, value in values.items():
        if type(value) not in (str, int, float, bool) or len(str(value)) > 500:
            raise ValueError(f'invalid scalar argument: {key}')
        kind = str(specs[key].get('type', '')).strip().casefold()
        valid = ((kind in {'str', 'string'} and type(value) is str and bool(value.strip()))
                 or (kind in {'int', 'integer'} and type(value) is int)
                 or (kind in {'float', 'number'} and type(value) in {int, float} and math.isfinite(value))
                 or (kind in {'bool', 'boolean'} and type(value) is bool))
        if not valid:
            raise ValueError(f'argument does not match declared type: {key}')
        if kind in {'int', 'integer'}:
            bounds = re.search(r'\bbetween\s+(\d+)\s+and\s+(\d+)\s+inclusive\b',
                               str(specs[key].get('constraints', '')), re.I)
            if bounds:
                low, high = map(int, bounds.groups())
                if low <= high <= 100 and not low <= value <= high:
                    raise ValueError(f'argument is outside declared bounds: {key}')
    return values


def validate_invocation_result(url: str, entry: dict, values: dict, result: object) -> None:
    if not isinstance(url, str) or not url:
        raise ValueError('verified summary has no website URL')
    errors = validate_tool_output(url, entry['candidate'], 'invocation', result, values)
    if errors:
        raise ValueError('invocation output failed contract checks: ' + '; '.join(errors))


def invoke(summary: Path, name: str, arguments: dict, timeout: int = 150) -> dict:
    entry = select_tool(summary, name)
    url = json.loads(inside_project(summary).read_text(encoding='utf-8')).get('url')
    values = prepare_arguments(entry, arguments)
    workspace = ROOT / 'invocations' / uuid4().hex
    workspace.mkdir(parents=True, exist_ok=False)
    script = workspace / 'final_script.py'
    shutil.copy2(inside_project(Path(entry['script'])), script)
    if hashlib.sha256(script.read_bytes()).hexdigest() != entry['script_sha256']:
        raise ValueError('script changed during copy; invocation stopped')
    output = workspace / 'output'
    flags = [part for key, value in values.items()
             for part in (f'--{key}', str(value).lower() if isinstance(value, bool) else str(value))]
    code, stdout, stderr = run_bounded(
        [str(SANDBOX), sys.executable, str(script), *flags, '--output-dir', str(output)],
        cwd=workspace, env={**os.environ, 'WORKSPACE_DIR': str(workspace)}, timeout=timeout)
    (workspace / 'stdout.txt').write_text(stdout, encoding='utf-8')
    (workspace / 'stderr.txt').write_text(stderr, encoding='utf-8')
    if code != 0:
        raise RuntimeError(f'tool exited {code}; evidence: {workspace}')
    result = read_result(checked_file(workspace, output / 'final_script_log.txt'))
    validate_invocation_result(url, entry, values, result)
    return {'tool': name, 'arguments': values, 'result': result, 'evidence_dir': str(workspace),
            'note': 'Previously verified on test cases; this invocation is not independently reverified.'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', required=True, type=Path)
    parser.add_argument('--tool', required=True)
    parser.add_argument('--arguments', required=True, help='JSON object of tool arguments')
    args = parser.parse_args()
    try:
        print(json.dumps(invoke(args.summary, args.tool, json.loads(args.arguments)), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired, RuntimeError) as error:
        print(f'invocation failed: {type(error).__name__}: {error}', file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
