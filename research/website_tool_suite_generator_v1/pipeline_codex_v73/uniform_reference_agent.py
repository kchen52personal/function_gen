"""Blind reference implementation agent; same procedure for every site.

The reference model is selected by the caller and never receives the generated
tool, its outputs, or the held input. Model-family independence depends on that
choice; correlated errors remain possible. The deterministic verifier owns the
acceptance decision.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sys

import yaml

from generic_grounding import check as grounding_check
from result_log import read_result
from uniform_reference_check import assess, comparison_mode, validate_tool_output
from codex_observation import OBSERVATION_TEMPLATE
from codex_artifact_guard import checked_file
from codex_prompt_escape import escape_jinja_literal
from codex_checkpoint import select_checkpoint
from codex_screenshots import ensure_screenshots

ROOT = Path('/shared/hans/research/website_tool_suite_generator_v1')
PIPELINE = Path(__file__).resolve().parent
KEY = Path('/shared/hans/.secrets/website_tool_api_key')
SANDBOX = PIPELINE / 'sandbox_launch.py'
REFERENCE_MODEL = 'claude-sonnet-4-5-20250929'


def visible_reference_disagreement(url: str, candidate: dict, case: dict,
                                   tool: dict, reference: dict, grounding: dict) -> list[str]:
    """Return only mismatching field names for a valid, grounded public case.

    This is an advisory, not an oracle: either implementation may be wrong.
    No reference values or hidden-case data are returned to the generator.
    """
    if (grounding.get('cases', {}).get('default', {}).get('status') != 'values_found'
            or validate_tool_output(url, candidate, 'default', tool, case)
            or validate_tool_output(url, candidate, 'default', reference, case)):
        return []
    returns = candidate['returns']
    left, right = tool['result'], reference['result']
    return sorted(field for field, kind in returns.items()
                  if comparison_mode({field: left[field]}, {field: right[field]},
                                     {**candidate, 'returns': {field: kind}}) is None)


def visible_reference_assessment(url: str, candidate: dict, case: dict,
                                 tool: dict, reference: dict, grounding: dict) -> dict:
    """Differentiate true agreement from an unusable public-case advisory."""
    if validate_tool_output(url, candidate, 'default', tool, case):
        reason = 'tool output invalid on visible case'
    elif validate_tool_output(url, candidate, 'default', reference, case):
        reason = 'reference output invalid on visible case'
    elif grounding.get('cases', {}).get('default', {}).get('status') != 'values_found':
        reason = 'reference output not grounded on visible case'
    else:
        fields = visible_reference_disagreement(url, candidate, case, tool, reference, grounding)
        if fields:
            return {'status': 'visible_reference_mismatch', 'counts_as_verified': False,
                    'mismatched_fields': fields,
                    'reason': 'independent implementation disagreed on visible field(s); hidden cases not run'}
        return {'status': 'visible_reference_agreement', 'counts_as_verified': False,
                'reason': 'grounded visible results agree; this is not independent verification'}
    result = {'status': 'visible_reference_unusable', 'counts_as_verified': False, 'reason': reason}
    if reason == 'reference output not grounded on visible case':
        # Keep bounded default-case diagnostics without exposing reference
        # answer values or inspecting any held case. Missing grounding remains
        # unusable; this is observability, not a weaker acceptance rule.
        default = grounding.get('cases', {}).get('default', {})
        if isinstance(default, dict):
            result['grounding_status'] = str(default.get('status', 'missing'))[:100]
            paths = default.get('missing_value_paths', [])
            if isinstance(paths, list):
                result['missing_value_paths'] = [p[:300] for p in paths[:20] if isinstance(p, str)]
            source = default.get('source_url')
            if isinstance(source, str):
                result['grounding_source_url'] = source[:2000]
    return result


def visible_value_previews(fields: list[str], tool: dict, reference: dict) -> dict:
    """Bound public scalar examples; never include other cases or nested data."""
    result = {}
    for field in fields[:10]:
        left = tool.get('result', {}).get(field)
        right = reference.get('result', {}).get(field)
        if type(left) in (str, int, float, bool) and type(right) in (str, int, float, bool):
            result[field] = {'tool': str(left)[:300], 'reference': str(right)[:300]}
    return result


def prompt(url: str, candidate: dict, cases: dict, *, independent_recon: bool = False) -> str:
    # Deliberately exclude tool code, tool outputs, and the held input.
    keys = ('name', 'goal', 'arguments', 'returns', 'boundary', 'success_contract')
    if not independent_recon:
        keys += ('proposal_evidence', 'codex_dom_evidence')
    task = {key: candidate.get(key) for key in keys}
    opening = ("In your FIRST action, independently inspect the relevant live page with Playwright "
               "and print bounded headings and nearby structure. In your SECOND action, save a "
               "complete final_script.py; do not return only a draft or prose." if independent_recon else
               "In your FIRST action, save a complete final_script.py; optional inspection may follow, "
               "but do not return only a draft or prose.")
    scope = ("Inspect the live page independently rather than relying on another agent's cited "
             "elements or captured markup." if independent_recon else
             "The shared captured DOM is a lead, not an answer: inspect the live page and verify scope.")
    actions = ("First inspect the live page with a bounded headless Playwright command. "
               "Use the installed Python Playwright library with chromium.launch(headless=True), "
               "for example in a quoted `python - <<'PY' ... PY` shell heredoc, and print only "
               "short relevant structure. Do not run playwright codegen, playwright open, "
               "playwright install, pip install, or any interactive/GUI browser command. "
               "In the next action write the complete script using a quoted heredoc like "
               "`cat > final_script.py <<'PY' ... PY`, then let the environment run the visible test."
               if independent_recon else
               "Your FIRST bash_command must first write the complete script with a quoted heredoc "
               "like `cat > final_script.py <<'PY' ... PY`, then let the environment run the visible test.")
    return f'''You are an independent website investigator. Do not assume any
other agent's answer. Inspect the public site and construct an independent
Python Playwright CLI that performs the described task and returns the exact
requested fields. Do not use hidden APIs, credentials, or state-changing
actions. {opening} The environment
automatically smoke-tests each changed script on the visible example and gives
you the output and schema errors. Fix failures before declaring done.
Arguments use exact --argument_name flags,
plus --output-dir. Parse those flags with argparse and obtain values from them;
do not read args.json or any task-specific file or embed the visible example as the input.
Boolean flags must accept true/false. Return JSON with
"parameters" echoing exactly the declared task arguments and no others (do
not include output-dir), "result" as a JSON object whose keys are exactly the
declared returns field names, and "source_url" pointing to the first-party page
from which the result values were actually extracted. After a navigation or
redirect, use the current URL of that page (or the relevant first-party frame),
not the initial homepage merely because the task started there. If no single
first-party source can support the reported result, fail rather than inventing
a source. A direct jump to one item is not a substitute for a requested list
or search-results workflow unless the declared contract explicitly permits it.
The result itself must not be a list, even when its individual fields
are list-valued. For example, returns {{"items":"list[str]"}} means the result
has the shape {{"items":["..."]}}, not ["..."] or [{{"items":"..."}}].
Do not add or remove result fields. Save before.png
and after.png in output-dir/screenshots. Write final_script_log.txt there with
first line "step 0 action: params:" and last line "final result: " plus JSON.
Fail if the task cannot be performed. The runner will test another input too.
Reject inputs outside the stated constraints with a nonzero exit rather than
silently substituting defaults, and validate before browsing.
Use domcontentloaded, never networkidle. Any locator used for a single-element
action or extraction must be explicitly unique: prefer role/name, ID, or a
scoped locator, and deliberately use .first or .nth(...) when necessary. Do
not use a comma-union CSS selector for a single-element action without
resolving it to one element.
Always use the parsed --output-dir for artifacts. If main is async, call it
with asyncio.run(main()). Resolve page links with urllib.parse.urljoin rather
than concatenating an origin and a possibly absolute href. Treat observed
selectors as leads and confirm them against the live DOM.
{scope} Extract values using a container that
matches the goal and boundary. Do not mix nested or adjacent sections into
a top-level list. Do not treat adjacent links as members of a named section
without inspecting the live structure.
For lists, include only actual task items: exclude navigation, archive, edit,
pagination, and other utility controls even if the site puts
them in the same container. Use the live page's actual structure and roles,
not an assumed textual prefix, punctuation pattern, or sample wording, to
distinguish task items from utilities. Preserve legitimate entries with unusual
spelling or punctuation; do not impose unrequested filters. For extracted prose and UI labels, collapse runs
of Unicode whitespace to one ASCII space and trim; preserve whitespace if the
task asks for exact formatting, code, or multiline text. Never normalize
parameters or URLs. This output contract does not permit changing facts or
ordering. If the required container or fields are absent, fail instead
of returning an empty or weaker result. A smoke-test PASS checks only format,
not semantics: inspect its output against the task and evidence.

Website: {url}
Task and requested output: {json.dumps(task, ensure_ascii=False)}
Visible example: {json.dumps(cases['default'], ensure_ascii=False)}

The action interface executes bash_command as POSIX shell, not as a Python
file. {actions} Never try to run final_script.py before writing it. Never place
raw Python source directly in bash_command without the heredoc shell wrapper.
Keep done=false while inspecting and writing; finish only after a passing test.
'''


def run(url: str, candidate: dict, cases: dict, outputs: dict, tool_grounding: dict,
        root: Path, env: dict[str, str], max_calls: int, run_command,
        model_name: str = REFERENCE_MODEL, visible_only: bool = False) -> dict:
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / 'agent'
    config = {
        'model': {'model_class': 'webwright_yibu_model.YibuWebWrightModel',
                  'model_name': model_name,
                  'openrouter_endpoint': 'https://yibuapi.com/v1/chat/completions',
                  'provider_require_parameters': False, 'api_key_file': str(KEY),
                  'observation_template': OBSERVATION_TEMPLATE,
                  'api_log_path': str(root / 'api_call.json'),
                  'spend_ledger_path': '/shared/hans/webarena/spend_ledger.jsonl',
                  'max_api_calls': max_calls, 'max_output_tokens': 6144,
                  'request_timeout_seconds': 180, 'attach_observation_screenshot': False},
        'environment': {'environment_class': 'webwright_safe_environment.SafeToolWorkspaceEnvironment',
                        'command_timeout_seconds': 120, 'shell': str(SANDBOX),
                        'browser_mode': 'local', 'output_truncation_chars': 12000,
                        'env': {'PLAYWRIGHT_BROWSERS_PATH': str(ROOT / '.playwright-browsers'),
                                'CODEX_VISIBLE_CASE_JSON': json.dumps(cases['default']),
                                'CODEX_RETURN_SCHEMA_JSON': json.dumps(candidate['returns'])}},
        'agent': {'agent_class': 'default', 'debug_log': True, 'step_limit': max_calls,
                  'require_self_reflection_success': False, 'summary_every_n_steps': 0,
                  'system_template': escape_jinja_literal(prompt(
                      url, candidate, cases,
                      independent_recon=env.get('CODEX_INDEPENDENT_REFERENCE_RECON') == '1')),
                  'instance_template': 'Task: {{ task }}\nStart URL: {{ start_url }}\nWorkspace: {{ workspace_dir }}'},
    }
    (root / 'config.yaml').write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding='utf-8')
    generation = run_command([sys.executable, str(PIPELINE / 'run_webwright_pilot.py'),
                              '--task', str(candidate['goal']), '--task-id', candidate['name'] + '_reference',
                              '--start-url', url, '--output-dir', str(workspace), '--config', str(root / 'config.yaml')],
                             cwd=ROOT, env=env, timeout=700, prefix=root / 'generation')
    script = workspace / 'final_script.py'
    try:
        checkpoint = select_checkpoint(root, workspace)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        return {'status': 'reference_failed', 'reason': f'invalid visible-smoke checkpoint: {error}',
                'counts_as_verified': False}
    if checkpoint is not None:
        script = checkpoint
    elif generation != 0 or not script.is_file():
        stderr_path = root / 'generation.stderr.txt'
        stderr = stderr_path.read_text(encoding='utf-8', errors='replace') if stderr_path.is_file() else ''
        provider_unavailable = generation == 124 or (
            'HTTPStatusError' in stderr and any(
                f"'{status} " in stderr for status in (502, 503, 504)))
        return {'status': 'reference_failed', 'reason': 'independent agent did not produce a reference script',
                'failure_kind': 'provider_unavailable' if provider_unavailable else 'generation_failed',
                'reference_model': model_name, 'counts_as_verified': False}
    try:
        script = checked_file(workspace, script)
    except (OSError, ValueError) as error:
        return {'status': 'reference_failed', 'reason': f'unsafe reference script: {error}',
                'counts_as_verified': False}
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    reference = {}
    for label, case in cases.items():
        run_dir = workspace / 'final_runs' / label
        flags = [part for key, value in case.items() for part in
                 (f'--{key}', str(value).lower() if isinstance(value, bool) else str(value))]
        code = run_command([str(SANDBOX), sys.executable, str(script), *flags, '--output-dir', str(run_dir)],
                           cwd=workspace, env={**env, 'WORKSPACE_DIR': str(workspace)},
                           timeout=150, prefix=root / f'execution_{label}')
        if code != 0:
            return {'status': 'reference_failed', 'reason': f'{label}: reference exit {code}',
                    'counts_as_verified': False}
        try:
            reference[label] = read_result(checked_file(workspace, run_dir / 'final_script_log.txt'))
            screenshot_errors = ensure_screenshots(workspace, run_dir)
            if screenshot_errors:
                raise ValueError('; '.join(screenshot_errors))
        except (OSError, ValueError) as error:
            return {'status': 'reference_failed', 'reason': f'{label}: {error}', 'counts_as_verified': False}
        if hashlib.sha256(script.read_bytes()).hexdigest() != digest:
            return {'status': 'reference_failed', 'reason': 'reference script changed during execution',
                    'counts_as_verified': False}
        if label == 'default' and visible_only:
            # Decide using the public case alone, before running any hidden
            # reference cases. A disagreement does not tell us which is right.
            default_grounding = asyncio.run(grounding_check(url, {'default': reference['default']}))
            advisory = visible_reference_assessment(url, candidate, case, outputs['default'],
                                                    reference['default'], default_grounding)
            if (advisory.get('status') == 'visible_reference_mismatch'
                    and env.get('CODEX_VISIBLE_REFERENCE_VALUES') == '1'):
                advisory['visible_value_preview'] = visible_value_previews(
                    advisory['mismatched_fields'], outputs['default'], reference['default'])
            advisory['reference_model'] = model_name
            return advisory
    if hashlib.sha256(script.read_bytes()).hexdigest() != digest:
        return {'status': 'reference_failed', 'reason': 'reference script changed during execution',
                'counts_as_verified': False}
    reference_grounding = asyncio.run(grounding_check(url, reference))
    report = assess(url, candidate, cases, outputs, reference,
                    tool_grounding, reference_grounding)
    report['reference_grounding'] = reference_grounding
    report['reference_script_sha256'] = digest
    report['reference_model'] = model_name
    report['script_selection'] = 'latest_visible_smoke_pass' if checkpoint else 'final_script'
    (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report
