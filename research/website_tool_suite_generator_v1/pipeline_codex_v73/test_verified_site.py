import os
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from run_verified_site import ROOT, SANDBOX, adapter_for, distinct
from select_candidates import select
from test_select_candidates import _candidate
import verify_amazon_product_details as details
import verify_amazon_product_search as search
import run_verified_site as pipeline
import invoke_verified_tool as invocation
import generic_grounding as grounding
import discover_test_cases as discovery
import uniform_reference_check as uniform_check
import uniform_reference_agent as reference_agent
import pytest
from result_log import read_result
import yaml


def test_reference_prompt_explicitly_requires_object_around_list_fields():
    prompt = reference_agent.prompt('https://example.test/', {
        'returns': {'items': 'list[str]'}, 'goal': 'List the observed items'
    }, {'default': {'limit': 2}})
    assert 'result itself must not be a list' in prompt
    assert '{"items":["..."]}' in prompt
    assert 'Do not add or remove result fields' in prompt


def test_goal_schema_mismatch_blocks_invocation_and_resume():
    candidate = {'name': 'category_lookup', 'goal': 'Retrieve a name, description, and portal URL',
                 'returns': {'name': 'str', 'heading': 'str', 'contents_page_url': 'str'}}
    entry = {'status': 'verified', 'counted': True, 'candidate': candidate}
    with tempfile.TemporaryDirectory(prefix='goal-mismatch-', dir=ROOT) as tmp:
        root = Path(tmp)
        summary = root / 'summary.json'
        summary.write_text(json.dumps({'pipeline_policy': pipeline.POLICY_VERSION, 'accepted': [entry]}))
        with pytest.raises(ValueError, match='does not cover its stated goal'):
            invocation.select_tool(summary, 'category_lookup')
        result = pipeline.recheck_saved_attempt('https://example.test/', root, 1, entry, root / 'recheck.json')
        assert result['status'] == 'not_recheckable'
        assert 'goal/schema mismatch' in result['reason']


def test_ambiguous_integer_bounds_block_invocation_and_resume():
    candidate = {'name': 'list_items', 'goal': 'List items', 'returns': {'items': 'list[str]'},
                 'arguments': [{'name': 'limit', 'type': 'int', 'default': 3,
                                'constraints': 'must be between 1 and 10'}],
                 'test_cases': {'default': {'limit': 3}, 'held': {'limit': 5}}}
    entry = {'status': 'verified', 'counted': True, 'candidate': candidate}
    with tempfile.TemporaryDirectory(prefix='ambiguous-bounds-', dir=ROOT) as tmp:
        root = Path(tmp)
        summary = root / 'summary.json'
        summary.write_text(json.dumps({'pipeline_policy': pipeline.POLICY_VERSION, 'accepted': [entry]}))
        with pytest.raises(ValueError, match='lacks supported boundary coverage'):
            invocation.select_tool(summary, 'list_items')
        result = pipeline.recheck_saved_attempt('https://example.test/', root, 1, entry, root / 'recheck.json')
        assert result['status'] == 'not_recheckable'
        assert 'boundary coverage' in result['reason']


def test_resume_feedback_excludes_dom_and_stays_under_provider_cap():
    summary = {'accepted': [{'candidate': {'name': f'tool_{i}', 'effect_key': f'key_{i}',
        'goal_class': 'lookup', 'goal': 'A' * 1000, 'returns': {'value': 'str'},
        'codex_dom_evidence': {'secret_marker': 'DOM_MARKER' * 10000}}} for i in range(10)],
        'rejected': [{'name': f'bad_{i}', 'reason': 'R' * 5000} for i in range(50)],
        'rounds': [{'screening_rejections': [{'name': f'bad_{i}', 'reason': 'S' * 5000}
                                              for i in range(20)]} for _ in range(2)]}
    feedback = pipeline.compact_proposal_feedback(summary)
    assert len(feedback) < 38000
    assert 'DOM_MARKER' not in feedback
    parsed = json.loads(feedback)
    assert parsed['accepted'][0]['name'] == 'tool_0'
    assert parsed['accepted'][0]['effect_key'] == 'key_0'
    assert len(parsed['previous_rejections_and_failures']) == 20
    assert len(parsed['previous_screening_errors']) == 10


def test_same_page_equivalent_contracts_are_not_counted_twice():
    one = {'name': 'browse_formats', 'effect_key': 'format.browse',
           'goal_class': 'format_browse', 'output_object_type': 'format_record',
           'goal': 'Retrieve the name and URL for a specific content format category from a contents page',
           'arguments': [{'name': 'format_type', 'type': 'str', 'default': 'overviews',
                          'constraints': 'one of: overviews, lists'}],
           'returns': {'format_name': 'str', 'format_url': 'str'},
           'proposal_evidence': {'resolved': {'page_url': 'https://example.test/contents'}}}
    two = {**one, 'name': 'get_format_info', 'effect_key': 'format.info',
           'goal_class': 'format_info', 'output_object_type': 'other_record',
           'goal': 'Retrieve the canonical display name and corresponding URL for a specified content format type from the contents page'}
    assert pipeline.same_page_contract(one, two)
    assert not pipeline.distinct(two, [{'candidate': one}])
    assert pipeline.repeated_attempt(two, [one])
    assert not pipeline.same_page_contract(one, {**two, 'returns': {'different': 'str'}})
    assert not pipeline.same_page_contract(one, {**two, 'proposal_evidence': {
        'resolved': {'page_url': 'https://example.test/other'}}})


def test_similar_headings_on_different_explicit_named_pages_are_not_deduplicated():
    first = {'name': 'main_header', 'goal': 'Retrieve the header text for a specific numbered section from the Wikipedia Main Page heading elements',
             'arguments': [{'name': 'section_index', 'type': 'int'}], 'returns': {'header_text': 'str'},
             'proposal_evidence': {'page_url': 'https://example.test/main'}}
    second = {'name': 'contents_header', 'goal': 'Retrieve the header text for a specific numbered section from the Wikipedia Contents page heading elements',
              'arguments': [{'name': 'section_index', 'type': 'int'}], 'returns': {'header_text': 'str'},
              'proposal_evidence': {'page_url': 'https://example.test/contents'}}
    assert pipeline.different_named_page_scopes(first, second)
    assert pipeline.distinct(second, [{'candidate': first}])
    assert not pipeline.repeated_attempt(second, [first])
    assert not pipeline.distinct({**second, 'name': 'main_header'}, [{'candidate': first}])
    assert not pipeline.different_named_page_scopes(first, {**second, 'proposal_evidence': {}})
    assert not pipeline.different_named_page_scopes(first, {**second, 'proposal_evidence': first['proposal_evidence']})


def test_indexed_string_subset_is_conservatively_flagged_as_overlap():
    section = {'page_url': 'https://example.test/', 'evidence_id': 'p00.s00.heading.002'}
    collection = {'goal': 'Retrieve up to N Did You Know fact strings from a section',
                  'arguments': [{'name': 'max_count', 'type': 'int',
                                 'constraints': 'between 1 and 5 inclusive'}],
                  'returns': {'dyk_facts': 'list[str]'}, 'proposal_evidence': section}
    single = {'goal': 'Retrieve one fact string by its zero-based position index',
              'arguments': [{'name': 'fact_index', 'type': 'int',
                             'constraints': 'between 0 and 4 inclusive'}],
              'returns': {'fact_text': 'str'}, 'proposal_evidence': section}
    assert pipeline.indexed_string_already_in_list(single, collection)
    position_only = {**single, 'goal': 'Retrieve a fact string by its position index'}
    assert pipeline.indexed_string_already_in_list(position_only, collection)
    assert not pipeline.indexed_string_already_in_list(
        {**single, 'goal': 'Retrieve a fact string by its one-based position index'}, collection)
    assert not pipeline.distinct(single, [{'candidate': collection}])
    assert not pipeline.indexed_string_already_in_list(collection, single)
    assert not pipeline.indexed_string_already_in_list(
        {**single, 'proposal_evidence': {**section, 'evidence_id': 'different'}}, collection)
    assert not pipeline.indexed_string_already_in_list(
        {**single, 'arguments': [{**single['arguments'][0], 'constraints': 'between 0 and 5 inclusive'}]}, collection)
    assert not pipeline.indexed_string_already_in_list(
        {**single, 'returns': {'fact_url': 'str'}}, collection)
    assert not pipeline.indexed_string_already_in_list(
        {**single, 'returns': {'fact_text': 'str', 'fact_url': 'str'}}, collection)
    assert not pipeline.indexed_string_already_in_list(
        {**single, 'goal': 'Retrieve a fact string by its one-based position index'}, collection)
    assert not pipeline.indexed_string_already_in_list(
        single, {**collection, 'returns': {'author_names': 'list[str]'}})
    assert not pipeline.indexed_string_already_in_list(
        single, {**collection, 'proposal_evidence': {**section, 'page_url': 'https://other.test/'}})


def test_enum_label_view_of_same_value_description_is_flagged():
    evidence = {'page_url': 'https://example.test/'}
    prior = {'goal': 'Retrieve a specific site statistic value and its description from the Main Page statistics section',
             'arguments': [{'name': 'stat_type', 'type': 'str',
                            'constraints': 'must be one of: active_editors, total_articles'}],
             'returns': {'stat_value': 'str', 'stat_description': 'str'}, 'proposal_evidence': evidence}
    candidate = {'goal': 'Retrieve a specific site statistic value and its label by metric type from the Main Page',
                 'arguments': [{'name': 'metric_type', 'type': 'str',
                                'constraints': 'one of: active_editors, total_articles'}],
                 'returns': {'stat_value': 'str', 'stat_label': 'str'}, 'proposal_evidence': evidence}
    assert pipeline.enum_value_label_overlaps_description(candidate, prior)
    assert not pipeline.distinct(candidate, [{'candidate': prior}])
    assert not pipeline.enum_value_label_overlaps_description(prior, candidate)
    assert not pipeline.enum_value_label_overlaps_description(
        {**candidate, 'proposal_evidence': {'page_url': 'https://elsewhere.test/'}}, prior)
    assert not pipeline.enum_value_label_overlaps_description(
        {**candidate, 'arguments': [{**candidate['arguments'][0], 'constraints': 'one of: active_editors, edits'}]}, prior)
    assert not pipeline.enum_value_label_overlaps_description(
        {**candidate, 'returns': {'stat_value': 'str', 'stat_label': 'str', 'extra': 'str'}}, prior)
    assert not pipeline.enum_value_label_overlaps_description(
        {**candidate, 'goal': 'List completely different bird species'}, prior)
    assert not pipeline.enum_value_label_overlaps_description(
        {**candidate, 'returns': {'stat_value': 'int', 'stat_label': 'str'}}, prior)


def test_small_explicit_enum_covers_remaining_choices_without_inventing_cases():
    candidate = {'arguments': [{'name': 'nav_item', 'type': 'str',
                               'constraints': 'one of: Main_Page, Random, Contents'}]}
    cases = {'default': {'nav_item': 'Main_Page'}, 'held': {'nav_item': 'Random'}}
    expanded = discovery.add_boundary_case(candidate, cases)
    assert expanded['stress_boundary'] == {'nav_item': 'Contents'}
    assert 'secondary_held' not in expanded
    no_colon = {'arguments': [{**candidate['arguments'][0],
                                'constraints': 'one of Main_Page, Random, Contents'}]}
    assert discovery.add_boundary_case(no_colon, cases)['stress_boundary'] == {'nav_item': 'Contents'}
    four = {'arguments': [{**candidate['arguments'][0],
                            'constraints': 'one of: Main_Page, Random, Contents, Help'}]}
    expanded = discovery.add_boundary_case(four, cases)
    assert expanded['stress_boundary'] == {'nav_item': 'Contents'}
    assert expanded['secondary_held'] == {'nav_item': 'Help'}
    many = {'arguments': [{**candidate['arguments'][0], 'constraints':
        'one of: Wikipedia, free, anyone can edit, English, SoHo Weekly News, The Village Voice, SoHo, punk'}]}
    many_cases = {'default': {'nav_item': 'Wikipedia'}, 'held': {'nav_item': 'English'}}
    expanded = discovery.add_boundary_case(many, many_cases)
    assert expanded['secondary_held'] == {'nav_item': 'SoHo'}
    assert len(expanded) == 4
    too_many = {'arguments': [{**candidate['arguments'][0],
                               'constraints': 'one of: ' + ', '.join('item' + str(i) for i in range(13))}]}
    assert discovery.add_boundary_case(too_many, cases) == cases
    examples = {'arguments': [{**candidate['arguments'][0],
                                'constraints': 'observed links, for example Main_Page, Random, Contents'}]}
    assert discovery.add_boundary_case(examples, cases) == cases
    assert discovery.add_boundary_case(candidate, {**cases, 'held': {'nav_item': 'Unknown'}}) == {
        **cases, 'held': {'nav_item': 'Unknown'}}


def test_explicit_exact_link_text_contract_rejects_substring_result():
    candidate = {'returns': {'link_text': 'str', 'link_url': 'str'},
                 'success_contract': 'returns both the exact visible link text and the full href URL'}
    case = {'link_text': 'SoHo'}
    result = {'parameters': case, 'source_url': 'https://example.test/',
              'result': {'link_text': 'SoHo Weekly News', 'link_url': 'https://example.test/news'}}
    errors = uniform_check.validate_tool_output('https://example.test/', candidate, 'secondary_held', result, case)
    assert any('exact link_text' in error for error in errors)
    result['result']['link_text'] = 'SoHo'
    assert not uniform_check.validate_tool_output('https://example.test/', candidate, 'secondary_held', result, case)


def test_legacy_migration_rejects_nested_and_input_echo_outputs():
    candidate = {'arguments': [{'name': 'kind'}], 'returns': {'title': 'str', 'links': 'list[str]'}}
    assert pipeline.legacy_scalar_candidate(candidate)
    assert not pipeline.legacy_scalar_candidate({**candidate, 'returns': {'items': 'list[object]'}})
    assert not pipeline.legacy_scalar_candidate({**candidate, 'returns': {'kind': 'str', 'title': 'str'}})
    assert not pipeline.legacy_scalar_candidate({**candidate, 'returns': {'metadata': 'object'}})


def test_primary_reference_is_from_different_generator_family():
    assert pipeline.primary_reference_model('qwen3.5-plus') == 'gpt-4.1'
    assert pipeline.primary_reference_model('qwen3.5-397b-a17b-thinking') == 'gpt-4.1'
    assert pipeline.primary_reference_model('GPT-4.1') == 'qwen3.5-plus'


def test_nested_search_results_require_nonempty_and_explicit_fields():
    candidate = {'returns': {'results': 'list[object]'},
                 'success_contract': "Returns a list of objects with 'title' and 'snippet' fields"}
    assert uniform_check.required_quoted_item_fields(candidate) == {'title', 'snippet'}
    def output(items):
        return {'parameters': {'query': 'test'}, 'result': {'results': items},
                'source_url': 'https://example.test/'}
    case = {'query': 'test'}
    assert not uniform_check.validate_tool_output('https://example.test/', candidate, 'default',
        output([{'title': 'Title', 'snippet': 'Description'}]), case)
    assert uniform_check.validate_tool_output('https://example.test/', candidate, 'default',
        output([{'title': 'Title', 'snippet': ''}]), case)
    assert uniform_check.validate_tool_output('https://example.test/', candidate, 'default',
        output([{'title': 'Title'}]), case)
    assert uniform_check.validate_tool_output('https://example.test/', candidate, 'default',
        output([{'title': 'Title', 'snippet': 'Description'}, {'title': 'Other'}]), case)
    assert uniform_check.required_quoted_item_fields({**candidate,
        'success_contract': 'Returns a list of results'}) == set()


def test_proposal_fallback_only_on_provider_unavailability(monkeypatch, tmp_path):
    calls = []
    def fake_propose(recon, output, **kwargs):
        calls.append(kwargs['model'])
        if len(calls) == 1:
            raise TimeoutError('read timed out')
        return {'candidates': []}
    monkeypatch.setattr(pipeline, 'propose_api', fake_propose)
    result, metadata = pipeline.proposal_with_provider_fallback(
        tmp_path / 'recon.json', tmp_path / 'proposal.json', 'qwen3.5-plus', 'feedback')
    assert result == {'candidates': []}
    assert calls == ['qwen3.5-plus', 'qwen3.5-flash']
    assert metadata['proposal_model_used'] == 'qwen3.5-flash'
    assert json.loads((tmp_path / 'primary_proposal_error.json').read_text())['model'] == 'qwen3.5-plus'
    calls.clear()
    def malformed(recon, output, **kwargs):
        calls.append(kwargs['model'])
        raise ValueError('malformed proposal')
    monkeypatch.setattr(pipeline, 'propose_api', malformed)
    with pytest.raises(ValueError, match='malformed'):
        pipeline.proposal_with_provider_fallback(
            tmp_path / 'recon.json', tmp_path / 'proposal.json', 'qwen3.5-plus', '')
    assert calls == ['qwen3.5-plus']


def test_nbsp_equivalence_is_explicit_narrow_and_never_mutates_values():
    candidate = {'goal': 'List facts', 'returns': {'heading': 'str', 'facts': 'list[str]'}}
    left = {'heading': 'Did you know\u00a0...', 'facts': ['first', 'second']}
    right = {'heading': 'Did you know ...', 'facts': ['first', 'second']}
    assert uniform_check.comparison_mode(left, right, candidate) == 'nbsp_space_equivalent'
    assert left['heading'] == 'Did you know\u00a0...'
    assert uniform_check.comparison_mode(right, right, candidate) == 'exact'
    assert uniform_check.comparison_mode(left, {**right, 'facts': ['second', 'first']}, candidate) is None
    assert uniform_check.comparison_mode(left, {**right, 'heading': 'Did you know!'}, candidate) is None
    assert uniform_check.comparison_mode(left, right, {**candidate, 'goal': 'Return exact raw text'}) is None
    url_candidate = {'goal': 'Get link', 'returns': {'target_url': 'str'}}
    assert uniform_check.comparison_mode({'target_url': 'x\u00a0y'}, {'target_url': 'x y'}, url_candidate) is None
    assert uniform_check.comparison_mode({'heading': 'a\tb', 'facts': ['x']},
                                         {'heading': 'a b', 'facts': ['x']}, candidate) is None
    cases = {'default': {'q': 1}, 'held': {'q': 2}}
    outputs = {label: {'parameters': params, 'source_url': 'https://example.test/',
                       'result': left if label == 'default' else right}
               for label, params in cases.items()}
    ground = {'cases': {label: {'status': 'values_found'} for label in cases}}
    report = uniform_check.assess('https://example.test/', candidate, cases, outputs, outputs, ground, ground)
    assert not report['counts_as_verified']
    assert any('did not change' in error for error in report['errors'])


def test_all_recorded_cases_including_secondary_held_are_grounded(monkeypatch):
    seen = []
    async def observe(url, source):
        seen.append(source)
        return {'page_url': source, 'visible_text': 'one two three four', 'links': []}
    monkeypatch.setattr(grounding, 'observe', observe)
    values = {'default': 'one', 'held': 'two', 'stress_boundary': 'three', 'secondary_held': 'four'}
    outputs = {label: {'source_url': 'https://example.test/page', 'result': {'value': value}}
               for label, value in values.items()}
    report = asyncio.run(grounding.check('https://example.test/', outputs))
    assert set(report['cases']) == set(values)
    assert all(case['status'] == 'values_found' for case in report['cases'].values())
    assert len(seen) == 4


def test_dom_id_grounding_only_for_explicit_id_fields(monkeypatch):
    async def observe(url, source):
        return {'page_url': source, 'visible_text': 'Culture and the arts',
                'links': [], 'ids': ['Culture_and_the_arts']}
    monkeypatch.setattr(grounding, 'observe', observe)
    def output(result):
        return {'source_url': 'https://example.test/page', 'result': result}
    good = {'default': output({'subject_heading_id': 'Culture_and_the_arts'}),
            'held': output({'id': 'Culture_and_the_arts'})}
    report = asyncio.run(grounding.check('https://example.test/', good))
    assert report['cases']['default']['status'] == 'values_found'
    assert report['cases']['default']['matched_dom_id_paths'] == ['$.subject_heading_id']
    bad = {'default': output({'description': 'Culture_and_the_arts'}),
           'held': output({'subject_heading_id': 'culture_and_the_arts'})}
    report = asyncio.run(grounding.check('https://example.test/', bad))
    assert report['cases']['default']['status'] == 'missing_values'
    assert report['cases']['held']['status'] == 'missing_values'


def test_formatted_numeric_grounding_is_typed_and_not_a_partial_number(monkeypatch):
    async def observe(url, source):
        return {'page_url': source, 'visible_text': 'Editors: 265,650; total: 1,265,650',
                'links': [], 'ids': []}
    monkeypatch.setattr(grounding, 'observe', observe)
    def output(result):
        return {'source_url': 'https://example.test/page', 'result': result}
    outputs = {'default': output({'count': 265650}), 'held': output({'count': 1265650})}
    report = asyncio.run(grounding.check('https://example.test/', outputs))
    assert report['cases']['default']['status'] == 'values_found'
    assert report['cases']['default']['matched_formatted_integer_paths'] == ['$.count']
    assert report['cases']['held']['status'] == 'values_found'
    assert grounding.integer_text_match('total: 1,265,650', 265650) == (False, False)
    assert grounding.integer_text_match('total: -265,650', 265650) == (False, False)
    assert grounding.integer_text_match('total: 265,650.5', 265650) == (False, False)
    assert grounding.integer_text_match('total: 2656500', 265650) == (False, False)
    assert grounding.integer_text_match('total: 265650.', 265650) == (True, False)
    strings = {'default': output({'count': '265650'}), 'held': output({'count': '1265650'})}
    report = asyncio.run(grounding.check('https://example.test/', strings))
    assert all(case['status'] == 'missing_values' for case in report['cases'].values())


def test_numeric_text_nodes_resolve_joined_body_text_without_partial_matches(monkeypatch):
    async def observe(url, source):
        return {'page_url': source, 'visible_text': 'active editors7,261,752 articles',
                'numeric_text_nodes': ['7,261,752'], 'links': [], 'ids': []}
    monkeypatch.setattr(grounding, 'observe', observe)
    def output(value):
        return {'source_url': 'https://example.test/page', 'result': {'count': value}}
    report = asyncio.run(grounding.check('https://example.test/', {
        'default': output(7261752), 'held': output(261752)}))
    assert report['cases']['default']['status'] == 'values_found'
    assert report['cases']['default']['matched_numeric_node_paths'] == ['$.count']
    assert report['cases']['held']['status'] == 'missing_values'


def test_grounding_allows_input_echo_only_with_separate_page_evidence(monkeypatch):
    async def observe(url, source):
        return {'page_url': source, 'visible_text': 'A real fact and the number 1',
                'numeric_text_nodes': [], 'links': [], 'ids': []}
    monkeypatch.setattr(grounding, 'observe', observe)
    def output(index, result):
        return {'source_url': 'https://example.test/page', 'parameters': {'fact_index': index}, 'result': result}
    valid = {'default': output(1, {'fact_index': 1, 'fact_text': 'A real fact'}),
             'held': output(3, {'fact_index': 3, 'fact_text': 'A real fact'})}
    report = asyncio.run(grounding.check('https://example.test/', valid))
    assert all(case['status'] == 'values_found' for case in report['cases'].values())
    assert report['cases']['held']['matched_parameter_echo_paths'] == ['$.fact_index']
    echo_only = {'default': output(1, {'fact_index': 1}), 'held': output(3, {'fact_index': 3})}
    report = asyncio.run(grounding.check('https://example.test/', echo_only))
    assert all(case['status'] == 'missing_values' for case in report['cases'].values())
    assert '$:no_page_derived_values' in report['cases']['default']['missing_value_paths']
    wrong_type = {'default': output(1, {'fact_index': True, 'fact_text': 'A real fact'}),
                  'held': output(3, {'fact_index': True, 'fact_text': 'A real fact'})}
    report = asyncio.run(grounding.check('https://example.test/', wrong_type))
    assert report['cases']['default']['matched_parameter_echo_paths'] == []


def test_both_agents_receive_same_nonsemantic_whitespace_and_list_contract():
    candidate = {'name': 'list_items', 'goal': 'List items', 'arguments': [],
                 'returns': {'items': 'list[str]'}}
    cases = {'default': {'limit': 2}, 'held': {'limit': 3}}
    recon = {'start_url': 'https://example.test/', 'allowed_host': 'example.test', 'pages': []}
    generator = pipeline.code_prompt('https://example.test/', candidate, recon, '', None, cases)
    reference = reference_agent.prompt('https://example.test/', candidate, cases)
    assert 'not hard-coded from remembered labels' in generator
    assert 'Fixed selectors or' in generator
    assert 'Never perform a state-changing action' in generator
    for prompt in (generator, reference):
        normalized = ' '.join(prompt.split())
        assert 'Unicode whitespace' in normalized
        assert 'preserve whitespace' in normalized
        assert 'Never normalize' in normalized
        assert 'utility controls' in normalized
        assert 'punctuation pattern' in normalized
        assert 'do not impose unrequested filters' in normalized
    assert uniform_check.exact_equal('A\u00a0B', 'A B') is False


def test_invocation_validates_argument_types_and_live_output_contract():
    entry = {'candidate': {'arguments': [
        {'name': 'limit', 'type': 'int', 'default': 3},
        {'name': 'query', 'type': 'str', 'default': 'books'},
        {'name': 'exact', 'type': 'bool', 'default': False},
        {'name': 'ratio', 'type': 'number', 'default': 1.5}],
        'returns': {'items': 'list[str]'}}}
    args = invocation.prepare_arguments(entry, {})
    assert args == {'limit': 3, 'query': 'books', 'exact': False, 'ratio': 1.5}
    for name, value in [('limit', True), ('limit', '3'), ('query', ''),
                        ('exact', 1), ('ratio', float('nan'))]:
        with pytest.raises(ValueError, match='declared type'):
            invocation.prepare_arguments(entry, {name: value})
    good = {'parameters': args, 'result': {'items': ['one']},
            'source_url': 'https://example.test/items'}
    invocation.validate_invocation_result('https://example.test/', entry, args, good)
    for bad in [{**good, 'parameters': {**args, 'limit': True}},
                {**good, 'result': {'items': 'one'}},
                {**good, 'source_url': 'https://evil.test/items'}]:
        with pytest.raises(ValueError, match='contract checks'):
            invocation.validate_invocation_result('https://example.test/', entry, args, bad)


def test_invalid_integer_boundaries_are_checked_and_invocation_rejects_them():
    candidate = {'arguments': [{'name': 'limit', 'type': 'int', 'default': 3,
                                'constraints': 'Must be between 1 and 10 inclusive'}]}
    cases = {'default': {'limit': 3}, 'held': {'limit': 5}}
    assert discovery.invalid_integer_cases(candidate, cases) == {
        'below_minimum': {'limit': 0}, 'above_maximum': {'limit': 11}}
    assert discovery.invalid_integer_cases({'arguments': [{**candidate['arguments'][0],
        'constraints': 'usually between 1 and 10'}]}, cases) == {}
    assert invocation.prepare_arguments({'candidate': candidate}, {'limit': 10}) == {'limit': 10}
    for value in (0, 11):
        with pytest.raises(ValueError, match='outside declared bounds'):
            invocation.prepare_arguments({'candidate': candidate}, {'limit': value})


def test_invalid_boundary_acceptance_fails_before_reference(monkeypatch):
    monkeypatch.setattr(pipeline, 'code_prompt', lambda *args: 'test prompt')
    accept_invalid = [True]
    def fake_run(command, *, cwd, env, timeout, prefix):
        if '--output-dir' in command and str(command[1]).endswith('run_webwright_pilot.py'):
            workspace = Path(command[command.index('--output-dir') + 1])
            workspace.mkdir()
            (workspace / 'final_script.py').write_text('# placeholder')
            return 0
        value = int(command[command.index('--limit') + 1])
        if value in (0, 11):
            return 0 if accept_invalid[0] else 2
        case_dir = Path(command[command.index('--output-dir') + 1])
        (case_dir / 'screenshots').mkdir(parents=True)
        for image in ('before.png', 'after.png'):
            (case_dir / 'screenshots' / image).write_bytes(b'image')
        (case_dir / 'final_script_log.txt').write_text(
            'step 0 action: params: test\nfinal result: ' + json.dumps({
                'parameters': {'limit': value}, 'result': {'value': value},
                'source_url': 'https://example.test/'}))
        return 0
    async def grounding(*args):
        return {'cases': {key: {'status': 'values_found'}
                          for key in ('default', 'held', 'stress_boundary', 'secondary_held')}}
    monkeypatch.setattr(pipeline, '_run', fake_run)
    monkeypatch.setattr(pipeline, 'check_generic_grounding', grounding)
    candidate = {'name': 'list_items', 'goal': 'List items', 'returns': {'value': 'int'},
                 'codex_dom_evidence': {'status': 'unavailable'},
                 'arguments': [{'name': 'limit', 'type': 'int', 'default': 3,
                                'constraints': 'between 1 and 10 inclusive'}],
                 'test_cases': {'default': {'limit': 3}, 'held': {'limit': 5}}}
    with tempfile.TemporaryDirectory(prefix='invalid-input-', dir=ROOT) as tmp:
        result = pipeline.attempt_product('https://example.test/', candidate, {},
                                          Path(tmp) / 'bad', {}, '', 1, None)
        assert result['status'] == 'invalid_input_failed'
        assert result['counted'] is False
        assert set(result['invalid_input_cases']) == {'below_minimum', 'above_maximum'}
        accept_invalid[0] = False
        result = pipeline.attempt_product('https://example.test/', candidate, {},
                                          Path(tmp) / 'good', {}, '', 1, None)
        assert result['status'] == 'executed_unverified'


def test_adapter_fails_closed_for_unrelated_site_or_intent():
    candidate = {'name': 'search_products', 'goal': 'Search products by query',
                 'arguments': [{'name': 'query', 'type': 'str'}], 'returns': {'title': 'str', 'url': 'str'}}
    assert adapter_for('https://www.amazon.sg/', candidate) is None
    assert adapter_for('https://example.com/', candidate) is None
    assert adapter_for('https://www.amazon.sg/', {**candidate, 'name': 'delete_products'}) is None
    assert adapter_for('https://www.amazon.sg/', {**candidate, 'arguments': [{'name': 'query', 'type': 'str'}, {'name': 'department', 'type': 'str'}]}) is None
    assert adapter_for('https://www.amazon.sg/', {**candidate, 'returns': {'title': 'str', 'url': 'str', 'price': 'float'}}) is None
    product = {'name': 'get_product_details', 'goal': 'Extract product details',
               'arguments': [{'name': 'asin', 'type': 'str'}],
               'returns': {'title': 'str', 'price': 'float', 'availability': 'str'}}
    assert adapter_for('https://www.amazon.sg/', product) is None
    ranking = {'name': 'get_best_sellers', 'goal': 'Get the best sellers by category',
               'arguments': [{'name': 'category', 'type': 'str'}, {'name': 'limit', 'type': 'int'}],
               'returns': {'products': 'list'}}
    assert adapter_for('https://www.amazon.sg/', ranking) is None
    assert adapter_for('https://example.com/', ranking) is None
    assert adapter_for('https://www.amazon.sg/', {**ranking, 'returns': {'products': 'list', 'price': 'float'}}) is None
    assert adapter_for('https://www.amazon.sg/', {**ranking, 'goal': 'Get new releases'}) is None


def test_active_pipeline_has_no_named_site_rules():
    files = ('run_verified_site.py', 'local_propose.py', 'api_propose.py', 'local_synthesize.py',
             'reconnaissance.py', 'select_candidates.py', 'generic_grounding.py',
             'discover_test_cases.py', 'uniform_reference_check.py', 'uniform_reference_agent.py',
             'run_detached_site_test.sh', 'invoke_verified_tool.py')
    for name in files:
        source = (pipeline.PIPELINE / name).read_text(encoding='utf-8').casefold()
        assert not any(site in source for site in ('amazon', 'wikipedia', 'netease', 'music.163.com'))


def test_old_certificates_cannot_be_invoked_as_uniformly_verified():
    with tempfile.TemporaryDirectory(prefix='uniform-invoke-', dir=ROOT) as tmp:
        summary = Path(tmp) / 'summary.json'
        summary.write_text(json.dumps({'accepted': [{'status': 'verified', 'counted': True,
                           'candidate': {'name': 'example'}}]}))
        with pytest.raises(ValueError, match='current blind-replication policy'):
            invocation.select_tool(summary, 'example')


def test_current_blind_replication_certificate_allows_unchanged_tool_selection():
    with tempfile.TemporaryDirectory(prefix='uniform-invoke-', dir=ROOT) as tmp:
        root = Path(tmp)
        script = root / 'tool.py'
        script.write_text('# verified script')
        report = root / 'verification.json'
        report.write_text(json.dumps({
            'status': uniform_check.VERIFIED_STATUS, 'counts_as_verified': True,
            'verification_version': uniform_check.VERIFICATION_VERSION}))
        entry = {'status': 'verified', 'counted': True,
                 'candidate': {'name': 'example'}, 'script': str(script),
                 'script_sha256': hashlib.sha256(script.read_bytes()).hexdigest(),
                 'verification': str(report)}
        summary = root / 'summary.json'
        summary.write_text(json.dumps({
            'pipeline_policy': pipeline.POLICY_VERSION, 'accepted': [entry]}))
        assert invocation.select_tool(summary, 'example') == entry
        script.write_text('# changed')
        with pytest.raises(ValueError, match='changed'):
            invocation.select_tool(summary, 'example')


def test_resume_reuses_only_intact_same_run_certificate():
    with tempfile.TemporaryDirectory(prefix='resume-integrity-', dir=ROOT) as tmp:
        root = Path(tmp)
        prior = root / 'prior'
        prior.mkdir()
        script = prior / 'tool.py'
        script.write_text('# original')
        report = prior / 'verification.json'
        report.write_text(json.dumps({
            'status': uniform_check.VERIFIED_STATUS, 'counts_as_verified': True,
            'verification_version': uniform_check.VERIFICATION_VERSION}))
        entry = {'status': 'verified', 'counted': True,
                 'candidate': {'name': 'example'}, 'script': str(script),
                 'script_sha256': hashlib.sha256(script.read_bytes()).hexdigest(),
                 'verification': str(report)}
        (prior / 'summary.json').write_text(json.dumps({
            'url': 'https://example.test/', 'pipeline_policy': pipeline.POLICY_VERSION,
            'accepted': [entry]}))
        result = pipeline.recheck_saved_attempt(
            'https://example.test/', prior, 1, entry, root / 'recheck.json')
        assert result['status'] == 'verified'
        assert result['recheck_type'] == 'saved_artifact_integrity_only'
        assert 'not re-executed' in result['recheck_limitation']
        assert result['recheck_original_run'] == str(prior)
        next_run = root / 'next_run'
        next_run.mkdir()
        (next_run / 'summary.json').write_text(json.dumps({
            'url': 'https://example.test/', 'pipeline_policy': pipeline.POLICY_VERSION,
            'accepted': [entry], 'resumed_from': str(prior)}))
        legacy = pipeline.recheck_saved_attempt(
            'https://example.test/', next_run, 1, entry, root / 'recheck_legacy.json')
        assert legacy['status'] == 'verified'
        assert legacy['recheck_original_run'] == str(prior)
        repeated = pipeline.recheck_saved_attempt(
            'https://example.test/', next_run, 1, result, root / 'recheck_again.json')
        assert repeated['status'] == 'verified'
        assert repeated['recheck_original_run'] == str(prior)
        wrong_url = pipeline.recheck_saved_attempt(
            'https://wrong.test/', next_run, 1, result, root / 'recheck_wrong_url.json')
        assert wrong_url['status'] == 'not_recheckable'
        script.write_text('# changed')
        rejected = pipeline.recheck_saved_attempt(
            'https://example.test/', next_run, 1, repeated, root / 'recheck_bad.json')
        assert rejected['status'] == 'not_recheckable'
        assert 'hash changed' in rejected['reason']


@pytest.mark.skip(reason='historical site-specific repair path disabled')
def test_repair_feedback_contains_default_dom_and_never_held_details():
    with tempfile.TemporaryDirectory(prefix='repair-evidence-', dir=ROOT) as tmp:
        script = Path(tmp) / 'script.py'
        script.write_text('# test')
        report = {'errors': ['default: wrong price', 'held: secret id'],
                  'fresh_live_reference': {
                      'default': {'price': '11.33', 'dom_evidence': {
                          'price_candidates': [{'html': 'priceToPay'}], 'specification_rows': []}},
                      'held': {'asin': 'SECRET_HELD_ID'}}}
        feedback = pipeline.repair_feedback(report, script)
        assert 'priceToPay' in feedback and '11.33' in feedback
        assert 'SECRET_HELD_ID' not in feedback and 'secret id' not in feedback


@pytest.mark.skip(reason='historical site-specific verifier certificates are no longer accepted')
def test_invocation_only_allows_unchanged_verified_tools_and_contract_args():
    with tempfile.TemporaryDirectory(prefix='invoke-test-', dir=ROOT) as tmp:
        root = Path(tmp)
        script = root / 'tool.py'
        script.write_text('# verified script')
        verification = root / 'verification.json'
        verification.write_text(json.dumps({'status': 'pass',
                                            'verification_version': search.VERIFICATION_VERSION}))
        entry = {'status': 'verified', 'counted': True, 'script': str(script),
                 'script_sha256': hashlib.sha256(script.read_bytes()).hexdigest(),
                 'verification': str(verification),
                 'adapter': 'amazon_product_search_v1',
                 'candidate': {'name': 'search_products', 'arguments': [{'name': 'query'}]},
                 'cases': {'default': {'query': 'mouse', 'limit': 8}}}
        summary = root / 'summary.json'
        summary.write_text(json.dumps({'accepted': [entry]}))
        selected = invocation.select_tool(summary, 'search_products')
        assert invocation.prepare_arguments(selected, {'query': 'keyboard'}) == {'query': 'keyboard', 'limit': 8}
        with pytest.raises(ValueError, match='argument not in'):
            invocation.prepare_arguments(selected, {'query': 'keyboard', 'extra': 'no'})
        with pytest.raises(ValueError, match='missing argument'):
            invocation.prepare_arguments(selected, {})
        script.write_text('# tampered')
        with pytest.raises(ValueError, match='changed'):
            invocation.select_tool(summary, 'search_products')
        script.write_text('# verified script')
        entry['adapter'] = 'amazon_product_details_v1'
        summary.write_text(json.dumps({'accepted': [entry]}))
        with pytest.raises(ValueError, match='outdated'):
            invocation.select_tool(summary, 'search_products')
        entry['counted'] = False
        summary.write_text(json.dumps({'accepted': [entry]}))
        with pytest.raises(ValueError, match='verified tool'):
            invocation.select_tool(summary, 'search_products')


def test_search_oracle_rejects_one_unsupported_result(monkeypatch):
    async def reference(query, limit):
        prefix = 'A' if query == 'wireless mouse' else 'B'
        return [{'asin': prefix + str(i).zfill(9), 'title': f'{query} item {i}'} for i in range(limit)]
    monkeypatch.setattr(search, 'live_reference', reference)
    with tempfile.TemporaryDirectory(prefix='search-oracle-', dir=ROOT) as tmp:
        runs = []
        for index, query in enumerate(('wireless mouse', 'usb keyboard')):
            run = Path(tmp) / str(index)
            (run / 'screenshots').mkdir(parents=True)
            for filename in ('before.png', 'after.png'):
                (run / 'screenshots' / filename).write_bytes(b'image')
            products = asyncio.run(reference(query, 4))
            if index == 0:
                products[-1] = {'asin': 'Z000000009', 'title': 'wireless mouse invented'}
            for item in products:
                item['url'] = 'https://www.amazon.sg/dp/' + item['asin'] + '/'
            (run / 'final_script_log.txt').write_text(
                'step 0 action: params: test\nfinal result: ' + json.dumps(
                    {'query': query, 'limit': 4, 'products': products}))
            runs.append(run)
        result = asyncio.run(search.verify(*runs))
        assert result['status'] == 'fail'
        assert any('3/4 ASINs overlap' in error for error in result['errors'])


def test_legacy_evidence_checker_never_certifies_ledger_claims():
    with tempfile.TemporaryDirectory(prefix='evidence-only-', dir=ROOT) as tmp:
        root = Path(tmp)
        tools = {}
        for index in range(10):
            name = f'tool_{index}'
            cases = {}
            for label in ('default', 'held'):
                run = root / name / label
                (run / 'screenshots').mkdir(parents=True)
                (run / 'final_script.py').write_text('# script')
                (run / 'final_script_log.txt').write_text('step 0 action: params: x\nfinal result: {}')
                for image in ('before.png', 'after.png'):
                    (run / 'screenshots' / image).write_bytes(b'image')
                cases[label + '_run'] = {'path': str(run.relative_to(root)),
                                         'arguments': {'value': label}, 'exit_code': 0}
            tools[name] = {'status': 'verified', 'counts_toward_final_ten': True, **cases}
        (root / 'site_manifest.json').write_text(json.dumps({'tools': [{'name': x} for x in tools]}))
        (root / 'live_verification.json').write_text(json.dumps({'tools': tools, 'verified_tools': 10}))
        result = subprocess.run([str(Path(os.sys.executable)), str(ROOT / 'pipeline/verify_live_evidence.py'), str(root)],
                                capture_output=True, text=True, check=False)
        data = json.loads(result.stdout)
        assert result.returncode != 0
        assert data['status'] == 'evidence_only' and data['verified_tools'] == 0


def test_generic_grounding_cannot_certify_even_when_values_match(monkeypatch):
    async def observe(start_url, source_url):
        return {'page_url': source_url, 'visible_text': 'This is a banana priced 12 dollars',
                'links': ['https://site.test/item/banana']}
    monkeypatch.setattr(grounding, 'observe', observe)
    output = {'source_url': 'https://site.test/item/banana',
              'result': {'name': 'banana', 'price': 12}}
    result = asyncio.run(grounding.check('https://site.test/', {'default': output, 'held': output}))
    assert result['status'] == 'grounding_only_not_verified'
    assert result['cases']['default']['status'] == 'values_found'
    # This value is present, but deliberately assigned to the wrong field;
    # the diagnostic must still not claim that the field is correct.
    wrong_meaning = {**output, 'result': {'price': 'banana'}}
    result = asyncio.run(grounding.check('https://site.test/', {'default': wrong_meaning, 'held': output}))
    assert result['cases']['default']['status'] == 'values_found'
    assert result['status'] == 'grounding_only_not_verified'
    missing = {**output, 'result': {'name': 'dragonfruit'}}
    result = asyncio.run(grounding.check('https://site.test/', {'default': missing, 'held': output}))
    assert result['cases']['default']['missing_value_paths'] == ['$.name']
    foreign = {**output, 'source_url': 'https://evil.test/private'}
    result = asyncio.run(grounding.check('https://site.test/', {'default': foreign, 'held': output}))
    assert result['cases']['default']['status'] == 'error'


def test_grounding_matches_exact_first_party_root_relative_links_only(monkeypatch):
    async def observe(start, source):
        return {'page_url': 'https://site.test/page', 'visible_text': 'Some page',
                'links': ['https://site.test/wiki/Main_Page', 'https://elsewhere.test/out']}
    monkeypatch.setattr(grounding, 'observe', observe)
    def check(result, params=None):
        output = {'source_url': 'https://site.test/page', 'parameters': params or {}, 'result': result}
        return asyncio.run(grounding.check('https://site.test/', {'default': output, 'held': output}))
    report = check({'nav_href': '/wiki/Main_Page'})
    assert report['status'] == 'grounding_only_not_verified'
    assert report['cases']['default']['status'] == 'values_found'
    assert report['cases']['default']['matched_relative_link_paths'] == ['$.nav_href']
    assert check({'unrelated': '/wiki/Main_Page'})['cases']['default']['status'] == 'missing_values'
    assert check({'nav_href': '/wiki/Elsewhere'})['cases']['default']['status'] == 'missing_values'
    assert check({'nav_href': '//elsewhere.test/out'})['cases']['default']['status'] == 'missing_values'
    assert check({'nav_href': '/wiki/Main_Page'}, {'nav_href': '/wiki/Main_Page'})['cases']['default']['status'] == 'missing_values'


def test_explicit_aria_attribute_field_needs_the_attribute_not_page_text(monkeypatch):
    observed = {'page_url': 'https://site.test/page', 'visible_text': 'Toggle section', 'links': [],
                'aria_attribute_values': {'aria-label': [], 'aria-description': []}}
    async def observe(start, source):
        return observed
    monkeypatch.setattr(grounding, 'observe', observe)
    output = {'source_url': 'https://site.test/page', 'parameters': {},
              'result': {'toggle_text': 'Toggle section', 'toggle_aria_label': 'Toggle section'}}
    def check():
        return asyncio.run(grounding.check('https://site.test/', {'default': output, 'held': output}))
    report = check()
    assert report['status'] == 'grounding_only_not_verified'
    assert report['cases']['default']['missing_value_paths'] == ['$.toggle_aria_label']
    observed['aria_attribute_values']['aria-label'] = ['Toggle section']
    report = check()
    assert report['cases']['default']['status'] == 'values_found'
    assert report['cases']['default']['matched_aria_attribute_paths'] == ['$.toggle_aria_label']
    observed['aria_attribute_values']['aria-label'] = []
    observed['aria_attribute_values']['aria-description'] = ['Toggle section']
    assert check()['cases']['default']['missing_value_paths'] == ['$.toggle_aria_label']


def test_old_aria_certificate_cannot_be_invoked_without_new_grounding():
    candidate = {'name': 'toggle', 'returns': {'toggle_aria_label': 'str'}}
    entry = {'candidate': candidate, 'status': 'verified', 'counted': True}
    with tempfile.TemporaryDirectory(prefix='old-aria-', dir=ROOT) as tmp:
        summary = Path(tmp) / 'summary.json'
        summary.write_text(json.dumps({'pipeline_policy': 'codex_v67_relative_link_grounding_tte',
                                       'accepted': [entry]}))
        with pytest.raises(ValueError, match='attribute-specific grounding'):
            invocation.select_tool(summary, 'toggle')


def test_uniform_reference_rejects_same_wrong_type_and_other_disagreements():
    cases = {'default': {'count': 1}, 'held': {'count': 2}}
    candidate = {'returns': {'price': 'float'}}
    outputs = {k: {'parameters': v, 'result': {'price': 'banana'},
                   'source_url': 'https://site.test/item'} for k, v in cases.items()}
    grounding = {'cases': {k: {'status': 'values_found'} for k in cases}}
    same_wrong = uniform_check.assess(
        'https://site.test/', candidate, cases, outputs, outputs, grounding, grounding)
    assert same_wrong['status'] == 'fail'
    assert same_wrong['counts_as_verified'] is False
    changed = {**outputs, 'held': {'parameters': cases['held'], 'result': {'price': 'changed'}}}
    assert uniform_check.assess(
        'https://site.test/', candidate, cases, changed, outputs, grounding, grounding)['status'] == 'fail'
    changed_type = {**outputs, 'default': {'parameters': {'count': True}, 'result': {'price': 'banana'}}}
    assert uniform_check.assess(
        'https://site.test/', candidate, cases, changed_type, outputs, grounding, grounding)['status'] == 'fail'


def test_uniform_reference_certifies_only_typed_grounded_input_sensitive_replication():
    candidate = {'returns': {'items': 'list[object]'}}
    cases = {'default': {'query': 'alpha'}, 'held': {'query': 'beta'}}
    outputs = {
        'default': {'parameters': cases['default'], 'result': {'items': [{'title': 'Alpha'}]},
                    'source_url': 'https://site.test/search?q=alpha'},
        'held': {'parameters': cases['held'], 'result': {'items': [{'title': 'Beta'}]},
                 'source_url': 'https://site.test/search?q=beta'},
    }
    grounding = {'cases': {label: {'status': 'values_found'} for label in cases}}
    report = uniform_check.assess(
        'https://site.test/', candidate, cases, outputs, outputs, grounding, grounding)
    assert report['status'] == uniform_check.VERIFIED_STATUS
    assert report['counts_as_verified'] is True
    constant = {label: {**value, 'result': {'items': [{'title': 'Same'}]}}
                for label, value in outputs.items()}
    assert uniform_check.assess(
        'https://site.test/', candidate, cases, constant, constant, grounding, grounding
    )['counts_as_verified'] is False
    offsite = {**outputs, 'held': {**outputs['held'], 'source_url': 'https://evil.test/'}}
    assert uniform_check.assess(
        'https://site.test/', candidate, cases, offsite, offsite, grounding, grounding
    )['counts_as_verified'] is False
    empty = {**outputs['default'], 'result': {'items': []}}
    assert uniform_check.validate_tool_output(
        'https://site.test/', candidate, 'default', empty, cases['default'])
    wrong_shape = {**outputs['default'], 'result': [{'title': 'Alpha'}]}
    assert uniform_check.validate_tool_output(
        'https://site.test/', candidate, 'default', wrong_shape, cases['default'])


def test_boundary_case_is_independent_and_checked_by_reference():
    candidate = {'arguments': [{'name': 'limit', 'type': 'int', 'default': 10,
                                'constraints': 'Must be between 1 and 20 inclusive'}],
                 'returns': {'items': 'list[str]'}}
    original = {'default': {'limit': 10}, 'held': {'limit': 5}}
    cases = discovery.add_boundary_case(candidate, original)
    assert cases['stress_boundary'] == {'limit': 20}
    assert cases['secondary_held'] == {'limit': 19}
    assert 'stress_boundary' not in original
    outputs = {label: {'parameters': case, 'result': {'items': [str(case['limit'])]},
                       'source_url': 'https://site.test/'} for label, case in cases.items()}
    grounded = {'cases': {label: {'status': 'values_found'} for label in cases}}
    report = uniform_check.assess('https://site.test/', candidate, cases, outputs, outputs,
                                  grounded, grounded)
    assert report['counts_as_verified']
    assert report['visible_development_cases'] == ['default', 'stress_boundary']
    assert report['held_out_cases'] == ['held', 'secondary_held']
    prompt = pipeline.code_prompt('https://site.test/', {
        **candidate, 'name': 'list_items', 'goal': 'List items'},
        {'start_url': 'https://site.test/', 'allowed_host': 'site.test', 'pages': []}, '', None, cases)
    assert '"limit": 20' in prompt
    assert '"limit": 19' not in prompt
    assert '"limit": 5' not in prompt
    wrong_reference = {**outputs, 'stress_boundary': {**outputs['stress_boundary'],
                       'result': {'items': ['wrong']}}}
    report = uniform_check.assess('https://site.test/', candidate, cases, outputs,
                                  wrong_reference, grounded, grounded)
    assert not report['counts_as_verified']
    assert any('stress_boundary' in error for error in report['errors'])
    assert discovery.add_boundary_case({**candidate, 'arguments': [{
        **candidate['arguments'][0], 'constraints': 'positive'}]}, original) == original


def test_reference_agent_prompt_never_receives_tool_output_or_held_case():
    candidate = {'name': 'fetch', 'goal': 'fetch data', 'arguments': [], 'returns': {'value': 'str'},
                 'untrusted_tool_output': 'SECRET_TOOL_OUTPUT',
                 'proposal_evidence': {'value': 'SECRET_SHARED_EVIDENCE'},
                 'codex_dom_evidence': {'value': 'SECRET_SHARED_DOM'},
                 'codex_return_dom_evidence': {'value': 'SECRET_SHARED_RETURN_DOM'}}
    cases = {'default': {'query': 'normal'}, 'held': {'query': 'SECRET_HELD'}}
    prompt = reference_agent.prompt('https://example.test/', candidate, cases, independent_recon=True)
    assert 'normal' in prompt
    assert 'SECRET_HELD' not in prompt
    assert 'SECRET_TOOL_OUTPUT' not in prompt
    assert 'SECRET_SHARED_EVIDENCE' not in prompt
    assert 'SECRET_SHARED_DOM' not in prompt
    assert 'SECRET_SHARED_RETURN_DOM' not in prompt
    assert 'In your FIRST action, independently inspect' in prompt
    assert 'In your SECOND' in prompt
    assert 'chromium.launch(headless=True)' in prompt
    assert 'Do not run playwright codegen' in prompt
    assert "cat > final_script.py <<'PY'" in prompt
    assert 'Never place raw Python source directly in' in ' '.join(prompt.split())
    shared = reference_agent.prompt('https://example.test/', candidate, cases)
    assert 'SECRET_SHARED_EVIDENCE' in shared and 'SECRET_SHARED_DOM' in shared
    assert 'SECRET_SHARED_RETURN_DOM' not in shared
    assert 'SECRET_HELD' not in shared and 'SECRET_TOOL_OUTPUT' not in shared


def test_independent_reference_run_uses_separate_workspace_and_different_model(monkeypatch):
    async def fake_grounding(url, outputs):
        return {'cases': {key: {'status': 'values_found'} for key in outputs}}
    monkeypatch.setattr(reference_agent, 'grounding_check', fake_grounding)
    cases = {'default': {'count': 1}, 'held': {'count': 2}}
    outputs = {label: {'parameters': case, 'result': {'value': f'value-{case["count"]}'},
                       'source_url': 'https://example.test/'} for label, case in cases.items()}
    def fake_run(command, *, cwd, env, timeout, prefix):
        if str(command[1]).endswith('run_webwright_pilot.py'):
            workspace = Path(command[command.index('--output-dir') + 1])
            workspace.mkdir()
            (workspace / 'final_script.py').write_text('# independent script')
        else:
            output = Path(command[command.index('--output-dir') + 1])
            (output / 'screenshots').mkdir(parents=True)
            for name in ('before.png', 'after.png'):
                (output / 'screenshots' / name).write_bytes(b'image')
            count = int(command[command.index('--count') + 1])
            (output / 'final_script_log.txt').write_text(
                'step 0 action: params: test\nfinal result: ' + json.dumps(
                    {'parameters': {'count': count}, 'result': {'value': f'value-{count}'},
                     'source_url': 'https://example.test/'}))
        return 0
    candidate = {'name': 'fetch', 'goal': 'fetch data', 'arguments': [{'name': 'count'}],
                 'returns': {'value': 'str'}}
    grounded = {'cases': {key: {'status': 'values_found'} for key in cases}}
    with tempfile.TemporaryDirectory(prefix='reference-agent-', dir=ROOT) as tmp:
        root = Path(tmp) / 'independent'
        report = reference_agent.run('https://example.test/', candidate, cases, outputs, grounded,
                                     root, {}, 1, fake_run)
        config = yaml.safe_load((root / 'config.yaml').read_text())
        assert 'SECRET_TOOL_OUTPUT' not in config['agent']['system_template']
        assert config['model']['model_name'] != 'qwen3.5-plus'
        assert report['status'] == uniform_check.VERIFIED_STATUS
        assert report['counts_as_verified'] is True


def test_reference_classifies_provider_outage_without_counting_it():
    def failed_run(command, *, cwd, env, timeout, prefix):
        prefix.with_suffix('.stderr.txt').write_text(
            "httpx.HTTPStatusError: Server error '503 Service Unavailable'")
        return 1
    cases = {'default': {'query': 'visible'}, 'held': {'query': 'hidden'}}
    candidate = {'name': 'search', 'goal': 'search', 'arguments': [], 'returns': {'value': 'str'}}
    with tempfile.TemporaryDirectory(prefix='reference-outage-', dir=ROOT) as tmp:
        report = reference_agent.run('https://example.test/', candidate, cases, {}, {},
                                     Path(tmp) / 'reference', {}, 1, failed_run)
        assert report['failure_kind'] == 'provider_unavailable'
        assert report['counts_as_verified'] is False


def test_accepted_functionality_cannot_be_added_twice():
    candidate = _candidate(0)
    assert distinct(candidate, [])
    assert not distinct(candidate, [{'candidate': candidate}])
    assert distinct(_candidate(1), [{'candidate': candidate}])


def test_failed_candidates_are_not_retried_or_revived():
    candidate = _candidate(0)
    renamed = {**candidate, 'name': 'another_name'}
    assert pipeline.repeated_attempt(renamed, [candidate])
    assert not pipeline.repeated_attempt(_candidate(1), [candidate])
    assert not pipeline.eligible_for_recheck({'status': 'verification_failed', 'counted': False})
    assert pipeline.eligible_for_recheck({'status': 'verified', 'counted': True})
    assert pipeline.attempted_names({'attempts': [{'candidate': candidate}]}) == {candidate['name']}
    process = subprocess.run([os.sys.executable, str(ROOT / 'pipeline/run_verified_site.py'),
                              'https://example.test/', '--repairs-per-candidate', '3'],
                             capture_output=True, text=True, check=False)
    assert process.returncode != 0
    assert 'between 0 and 2' in process.stderr
    assert pipeline.evolvable_failure({
        'status': 'execution_failed', 'cases': {'default': {}}, 'repair_context': 'trace'})
    assert not pipeline.evolvable_failure({
        'status': 'execution_failed', 'cases': {'default': {}, 'held': {}},
        'repair_context': 'hidden failure must not be fed back'})
    assert not pipeline.evolvable_failure({
        'status': 'verification_failed', 'repair_context': 'oracle disagreement'})


def test_loop_discards_failure_and_continues_to_distinct_candidates(monkeypatch):
    candidates = [_candidate(i) for i in range(3)]
    calls = []
    monkeypatch.setattr(pipeline, 'select', lambda *args, **kwargs: ({'tools': candidates}, {}))
    def fake_attempt(url, candidate, recon, root, env, feedback, max_calls, adapter, experimental_reference=False):
        calls.append(candidate['name'])
        if len(calls) == 1:
            return {'status': 'verification_failed', 'counted': False, 'reason': 'failed live check'}
        return {'status': 'verified', 'counted': True}
    monkeypatch.setattr(pipeline, 'attempt_product', fake_attempt)
    with tempfile.TemporaryDirectory(prefix='discard-loop-', dir=ROOT) as tmp:
        base = Path(tmp)
        prior = base / 'prior'
        (prior / 'recon').mkdir(parents=True)
        (prior / 'round_01').mkdir()
        (prior / 'summary.json').write_text(json.dumps({'url': 'https://example.test/', 'attempts': [],
                                                        'pipeline_policy': pipeline.POLICY_VERSION}))
        (prior / 'recon/recon.json').write_text('{}')
        (prior / 'round_01/proposal.json').write_text(json.dumps({'candidates': candidates}))
        output = base / 'output'
        monkeypatch.setattr(pipeline.os, 'environ', os.environ.copy())
        monkeypatch.setattr(os.sys, 'argv', ['run_verified_site.py', 'https://example.test/',
                           '--resume-from', str(prior), '--output', str(output), '--target', '2',
                           '--max-rounds', '1', '--max-attempts', '3',
                           '--repairs-per-candidate', '0'])
        pipeline.main()
        summary = json.loads((output / 'summary.json').read_text())
        assert calls == [c['name'] for c in candidates]
        assert summary['verified_count'] == 2
        assert summary['attempts'][0]['status'] == 'verification_failed'
        assert [x['candidate']['name'] for x in summary['accepted']] == calls[1:]
        assert [x['name'] for x in summary['attempted_candidates']] == calls


def test_loop_evolves_default_failure_once_without_counting_extra_candidate(monkeypatch):
    candidate = _candidate(0)
    calls = []
    monkeypatch.setattr(pipeline, 'select', lambda *args, **kwargs: ({'tools': [candidate]}, {}))
    def fake_attempt(url, candidate, recon, root, env, feedback, max_calls, adapter,
                     experimental_reference=False):
        calls.append({'root': root.name, 'feedback': feedback})
        if len(calls) == 1:
            return {'status': 'execution_failed', 'counted': False,
                    'reason': 'default crashed', 'cases': {'default': {}},
                    'repair_context': 'VISIBLE_DEFAULT_TRACE'}
        return {'status': 'verified', 'counted': True, 'reason': 'passed'}
    monkeypatch.setattr(pipeline, 'attempt_product', fake_attempt)
    with tempfile.TemporaryDirectory(prefix='evolution-loop-', dir=ROOT) as tmp:
        base = Path(tmp)
        prior = base / 'prior'
        (prior / 'recon').mkdir(parents=True)
        (prior / 'round_01').mkdir()
        (prior / 'summary.json').write_text(json.dumps({
            'url': 'https://example.test/', 'attempts': [],
            'pipeline_policy': pipeline.POLICY_VERSION}))
        (prior / 'recon/recon.json').write_text('{}')
        (prior / 'round_01/proposal.json').write_text(json.dumps({'candidates': [candidate]}))
        output = base / 'output'
        monkeypatch.setattr(pipeline.os, 'environ', os.environ.copy())
        monkeypatch.setattr(os.sys, 'argv', ['run_verified_site.py', 'https://example.test/',
                           '--resume-from', str(prior), '--output', str(output), '--target', '1',
                           '--max-rounds', '1', '--max-attempts', '1',
                           '--repairs-per-candidate', '1'])
        pipeline.main()
        summary = json.loads((output / 'summary.json').read_text())
        assert [call['root'] for call in calls] == ['version_00', 'version_01']
        assert 'VISIBLE_DEFAULT_TRACE' in calls[1]['feedback']
        assert summary['verified_count'] == 1 and len(summary['attempts']) == 1
        assert summary['attempts'][0]['evolution_count'] == 1


def test_replay_inputs_compares_fresh_without_importing_prior_attempts(monkeypatch):
    candidate = _candidate(0)
    calls = []
    monkeypatch.setattr(pipeline, 'select', lambda *args, **kwargs: ({'tools': [candidate]}, {}))
    def fake_attempt(url, candidate, recon, root, env, feedback, max_calls, adapter,
                     experimental_reference=False):
        calls.append(candidate['name'])
        return {'status': 'execution_failed', 'counted': False, 'reason': 'failed'}
    monkeypatch.setattr(pipeline, 'attempt_product', fake_attempt)
    with tempfile.TemporaryDirectory(prefix='replay-inputs-', dir=ROOT) as tmp:
        base = Path(tmp)
        prior = base / 'prior'
        (prior / 'recon').mkdir(parents=True)
        (prior / 'round_01').mkdir()
        (prior / 'summary.json').write_text(json.dumps({
            'url': 'https://example.test/', 'pipeline_policy': 'older_comparison_policy',
            'attempted_candidates': [candidate], 'attempts': [{'candidate': candidate, 'counted': False}]}))
        (prior / 'recon/recon.json').write_text('{}')
        (prior / 'round_01/proposal.json').write_text(json.dumps({'candidates': [candidate]}))
        output = base / 'output'
        monkeypatch.setattr(pipeline.os, 'environ', os.environ.copy())
        monkeypatch.setattr(os.sys, 'argv', ['run_verified_site.py', 'https://example.test/',
                           '--replay-inputs-from', str(prior), '--output', str(output),
                           '--target', '1', '--max-rounds', '1', '--max-attempts', '1',
                           '--repairs-per-candidate', '0'])
        pipeline.main()
        summary = json.loads((output / 'summary.json').read_text())
        assert calls == [candidate['name']]
        assert summary['replay_inputs_from'] == str(prior)
        assert len(summary['attempts']) == 1


@pytest.mark.skip(reason='historical site-specific prompt disabled')
def test_held_product_id_is_not_given_to_the_code_generator():
    candidate = {'name': 'get_product_details', 'goal': 'product details',
                 'arguments': [{'name': 'asin', 'type': 'str'}]}
    recon = {'start_url': 'https://www.amazon.sg/', 'allowed_host': 'www.amazon.sg',
             'pages': [], 'repeated_first_party_routes': []}
    prompt = pipeline.code_prompt('https://www.amazon.sg/', candidate, recon, '',
                                  'amazon_product_details_v1',
                                  {'default': {'asin': 'DEFAULTID'}, 'held': {'asin': 'HELDSECRET'}})
    assert 'DEFAULTID' in prompt
    assert 'HELDSECRET' not in prompt


def test_generic_test_cases_are_prepared_without_code_writer_and_held_is_not_in_prompt():
    candidate = {'name': 'get_recent_changes', 'goal': 'list recent changes',
                 'arguments': [{'name': 'limit', 'type': 'int', 'default': '50',
                                'constraints': 'Positive integer between 1 and 50'},
                               {'name': 'hide_bots', 'type': 'bool', 'default': 'true',
                                'constraints': 'true or false'}]}
    cases = discovery.prepare(candidate)
    assert cases == {'default': {'limit': 50, 'hide_bots': True},
                     'held': {'limit': 49, 'hide_bots': True}}
    recon = {'start_url': 'https://example.test/', 'allowed_host': 'example.test',
             'pages': [], 'repeated_first_party_routes': []}
    prompt = pipeline.code_prompt('https://example.test/', candidate, recon, '', None, cases)
    assert json.dumps(cases['default']) in prompt
    assert json.dumps(cases['held']) not in prompt
    with pytest.raises(ValueError, match='no safely inferable'):
        discovery.prepare({'arguments': [{'name': 'query', 'type': 'str', 'default': 'foo'}]})


def test_proposal_supplied_cases_are_typed_and_held_is_hidden_from_code_writer():
    candidate = {'name': 'search_items', 'goal': 'search items',
                 'arguments': [{'name': 'query', 'type': 'str', 'default': 'alpha',
                                'constraints': 'non-empty string'},
                               {'name': 'limit', 'type': 'int', 'default': 5,
                                'constraints': 'positive integer'}],
                 'test_cases': {'default': {'query': 'alpha', 'limit': 5},
                                'held': {'query': 'SECRET_HELD', 'limit': 5}}}
    cases = discovery.prepare(candidate)
    assert cases['held']['query'] == 'SECRET_HELD'
    prompt = pipeline.code_prompt('https://example.test/', candidate,
        {'start_url': 'https://example.test/', 'allowed_host': 'example.test',
         'pages': [], 'repeated_first_party_routes': []}, '', None, cases)
    assert 'alpha' in prompt
    assert 'SECRET_HELD' not in prompt
    assert 'Hidden duplicate inputs must not be chosen over visible ones' in prompt
    assert 'Do not turn an example format into an exhaustive rule' in prompt
    with pytest.raises(ValueError, match='exactly match'):
        discovery.prepare({**candidate, 'test_cases': {
            'default': {'query': 'different', 'limit': 5},
            'held': {'query': 'SECRET_HELD', 'limit': 5}}})


def test_partial_selection_does_not_cap_candidate_queue_at_ten():
    candidates = [_candidate(i) for i in range(10)]
    extra = _candidate(0)
    extra.update(name='retrieve_genres', effect_key='genres.records',
                 goal_class='genres_retrieval', output_object_type='genres_records',
                 goal='Retrieve structured genres records')
    candidates.append(extra)
    proposal = {'site_id': 'example', 'site_name': 'Example',
                'start_url': 'https://example.test/', 'allowed_hosts': ['example.test'],
                'candidates': candidates}
    manifest, _ = select(proposal, require_exact_count=False)
    assert manifest['tool_count'] == 11


def test_generated_command_cannot_read_project_secrets_or_write_elsewhere():
    with tempfile.TemporaryDirectory(prefix='sandbox-test-', dir=ROOT) as work:
        script = ('from pathlib import Path\n'
                  'for path,mode in [("/shared/hans/.secrets/website_tool_api_key","r"),'
                  '("/shared/hans/progress/1112_work_log.md","a")]:\n'
                  ' try:\n  open(path,mode).close()\n  print("UNEXPECTED_ACCESS")\n'
                  ' except PermissionError:\n  print("BLOCKED")\n')
        env = {**os.environ, 'WORKSPACE_DIR': work}
        result = subprocess.run([str(SANDBOX), '/shared/miniconda3/bin/python', '-c', script],
                                env=env, text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ['BLOCKED', 'BLOCKED']


def test_details_oracle_rejects_wrong_fields(monkeypatch):
    async def fake_reference(asin):
        return {'asin': asin, 'title': 'Mouse', 'price': '$10', 'availability': 'In stock',
                'seller': 'Seller', 'features': ['Wireless mouse feature'],
                'specifications': {'Brand': 'Example'}}
    monkeypatch.setattr(details, 'live_reference', fake_reference)
    with tempfile.TemporaryDirectory(prefix='oracle-test-', dir=ROOT) as tmp:
        runs, cases = {}, {'default': 'ASIN1', 'held': 'ASIN2'}
        for label, asin in cases.items():
            run = Path(tmp) / label
            (run / 'screenshots').mkdir(parents=True)
            for filename in ('before.png', 'after.png'):
                (run / 'screenshots' / filename).write_bytes(b'image')
            value = asyncio.run(fake_reference(asin))
            value['price'] = 10.0
            (run / 'final_script_log.txt').write_text(
                'step 0 action: params: test\nfinal result: ' + json.dumps(value))
            runs[label] = run
        assert asyncio.run(details.verify(runs, cases))['status'] == 'pass'
        log = runs['held'] / 'final_script_log.txt'
        log.write_text(log.read_text().replace('Seller', 'Wrong seller'))
        failed = asyncio.run(details.verify(runs, cases))
        assert failed['status'] == 'fail'
        assert any('seller' in error for error in failed['errors'])
        # A complete-looking overview must not pass if the independent page
        # contains an additional specification under an expandable section.
        log.write_text(log.read_text().replace('Wrong seller', 'Seller'))
        async def expanded_reference(asin):
            value = await fake_reference(asin)
            value['specifications']['Power Source'] = 'Battery Powered'
            return value
        monkeypatch.setattr(details, 'live_reference', expanded_reference)
        failed = asyncio.run(details.verify(runs, cases))
        assert failed['status'] == 'fail'
        assert any('specifications' in error for error in failed['errors'])


def test_result_reader_supports_multiline_json():
    with tempfile.TemporaryDirectory(prefix='log-test-', dir=ROOT) as tmp:
        path = Path(tmp) / 'result.log'
        path.write_text('step 0 action: params: example\nfinal result: ' + json.dumps({'value': 3}, indent=2))
        assert read_result(path) == {'value': 3}
        path.write_text('step 0 action: start params: example\nfinal result: ' + json.dumps({'value': 4}, indent=2))
        assert read_result(path) == {'value': 4}
        path.write_text('step 0 action: start params: example\nstep 11 action: final_result params: ' + json.dumps({'value': 5}))
        assert read_result(path) == {'value': 5}


def test_tool_counts_only_after_blind_reference_verifier(monkeypatch):
    monkeypatch.setattr(pipeline, 'code_prompt', lambda *args: 'test prompt')
    async def grounding_found(*args):
        return {'status': 'grounding_only_not_verified', 'cases': {
            'default': {'status': 'values_found'}, 'held': {'status': 'values_found'}}}
    monkeypatch.setattr(pipeline, 'check_generic_grounding', grounding_found)
    def fake_run(command, *, cwd, env, timeout, prefix):
        if '--output-dir' in command and str(command[1]).endswith('run_webwright_pilot.py'):
            workspace = Path(command[command.index('--output-dir') + 1])
            workspace.mkdir()
            (workspace / 'final_script.py').write_text('# placeholder')
        else:
            case_dir = Path(command[command.index('--output-dir') + 1])
            (case_dir / 'screenshots').mkdir(parents=True)
            for image in ('before.png', 'after.png'):
                (case_dir / 'screenshots' / image).write_bytes(b'image')
            query = command[command.index('--query') + 1]
            (case_dir / 'final_script_log.txt').write_text(
                'step 0 action: params: test\nfinal result: ' +
                json.dumps({'parameters': {'query': query}, 'result': {'value': query},
                            'source_url': 'https://example.test/'}))
        return 0
    monkeypatch.setattr(pipeline, '_run', fake_run)
    with tempfile.TemporaryDirectory(prefix='loop-test-', dir=ROOT) as tmp:
        result = pipeline.attempt_product(
            'https://example.test/', {'name': 'search_items', 'goal': 'Search items',
            'returns': {'value': 'str'},
            'arguments': [{'name': 'query', 'type': 'str', 'default': 'one',
                           'constraints': 'one of: one,two'}]}, {}, Path(tmp) / 'attempt', {}, '', 1, None)
        assert result['status'] == 'executed_unverified'
        assert result['counted'] is False
        monkeypatch.setattr(pipeline, 'run_independent_reference', lambda *args, **kwargs: {
            'status': uniform_check.VERIFIED_STATUS, 'counts_as_verified': True})
        agreed = pipeline.attempt_product(
            'https://example.test/', {'name': 'search_items', 'goal': 'Search items',
            'returns': {'value': 'str'},
            'arguments': [{'name': 'query', 'type': 'str', 'default': 'one',
                           'constraints': 'one of: one,two'}]}, {}, Path(tmp) / 'attempt_agreed',
            {}, '', 1, None, experimental_reference=True)
        assert agreed['status'] == 'verified' and agreed['counted'] is True
        reference_calls = []
        def unavailable_then_pass(*args, **kwargs):
            reference_calls.append((kwargs.get('model_name', 'primary'), args[7]))
            if len(reference_calls) == 1:
                return {'status': 'reference_failed', 'failure_kind': 'provider_unavailable',
                        'counts_as_verified': False}
            return {'status': uniform_check.VERIFIED_STATUS, 'counts_as_verified': True}
        monkeypatch.setattr(pipeline, 'run_independent_reference', unavailable_then_pass)
        fallback = pipeline.attempt_product(
            'https://example.test/', {'name': 'search_items', 'goal': 'Search items',
            'returns': {'value': 'str'},
            'arguments': [{'name': 'query', 'type': 'str', 'default': 'one',
                           'constraints': 'one of: one,two'}]}, {}, Path(tmp) / 'attempt_fallback',
            {'CODEX_REFERENCE_MAX_CALLS': '6'}, '', 1, None, experimental_reference=True)
        assert reference_calls == [('gpt-4.1', 6), (pipeline.REFERENCE_MODEL, 6)]
        assert fallback['status'] == 'verified' and fallback['counted'] is True
        reference_calls.clear()
        gpt_generated = pipeline.attempt_product(
            'https://example.test/', {'name': 'search_items', 'goal': 'Search items',
            'returns': {'value': 'str'},
            'arguments': [{'name': 'query', 'type': 'str', 'default': 'one',
                           'constraints': 'one of: one,two'}]}, {}, Path(tmp) / 'attempt_gpt',
            {'CODEX_REFERENCE_MAX_CALLS': '6', 'CODEX_GENERATOR_MODEL': 'gpt-4.1'},
            '', 1, None, experimental_reference=True)
        assert reference_calls == [('qwen3.5-plus', 6), (pipeline.REFERENCE_MODEL, 6)]
        assert gpt_generated['status'] == 'verified' and gpt_generated['counted'] is True
        async def grounding_failed(*args):
            return {'status': 'grounding_only_not_verified', 'cases': {
                'default': {'status': 'missing_values'}, 'held': {'status': 'values_found'}}}
        monkeypatch.setattr(pipeline, 'check_generic_grounding', grounding_failed)
        result = pipeline.attempt_product(
            'https://example.test/', {'name': 'search_items', 'goal': 'Search items',
            'returns': {'value': 'str'},
            'arguments': [{'name': 'query', 'type': 'str', 'default': 'one',
                           'constraints': 'one of: one,two'}]}, {}, Path(tmp) / 'attempt2', {}, '', 1, None)
        assert result['status'] == 'grounding_failed' and result['counted'] is False


def test_failed_default_execution_skips_held_case(monkeypatch):
    monkeypatch.setattr(pipeline, 'code_prompt', lambda *args: 'test prompt')
    executed = []
    def fake_run(command, *, cwd, env, timeout, prefix):
        if '--output-dir' in command and str(command[1]).endswith('run_webwright_pilot.py'):
            workspace = Path(command[command.index('--output-dir') + 1])
            workspace.mkdir()
            (workspace / 'final_script.py').write_text('# placeholder')
            return 0
        executed.append(command[command.index('--query') + 1])
        return 1
    monkeypatch.setattr(pipeline, '_run', fake_run)
    with tempfile.TemporaryDirectory(prefix='early-fail-', dir=ROOT) as tmp:
        result = pipeline.attempt_product(
            'https://example.test/', {'name': 'search_items', 'goal': 'Search items',
            'returns': {'value': 'str'},
            'arguments': [{'name': 'query', 'type': 'str', 'default': 'one',
                           'constraints': 'one of: one,two'}]}, {}, Path(tmp) / 'attempt', {}, '', 1, None)
        assert result['status'] == 'execution_failed'
        assert executed == ['one']


@pytest.mark.skip(reason='historical site-specific recheck disabled')
def test_recheck_requires_clean_runs_and_hidden_held_input(monkeypatch):
    async def fake_verifier(*args):
        return {'status': 'pass', 'errors': []}
    monkeypatch.setattr(pipeline, 'verify_product_details', fake_verifier)
    candidate = {'name': 'get_product_details', 'goal': 'Extract product details',
                 'arguments': [{'name': 'asin', 'type': 'str'}],
                 'returns': {'title': 'str', 'price': 'float', 'availability': 'str'}}
    with tempfile.TemporaryDirectory(prefix='recheck-test-', dir=ROOT) as tmp:
        prior = Path(tmp) / 'prior'
        root = prior / 'attempt_01'
        agent = root / 'agent'
        agent.mkdir(parents=True)
        script = agent / 'final_script.py'
        script.write_text('# test')
        config = {'agent': {'system_template': 'use DEFAULT', 'instance_template': 'run'}}
        (root / 'config.yaml').write_text(yaml.safe_dump(config))
        cases = {'default': {'asin': 'DEFAULT', 'exit_code': 0},
                 'held': {'asin': 'HELDID', 'exit_code': 0}}
        for label, case in cases.items():
            run = agent / 'final_runs' / label
            run.mkdir(parents=True)
            (run / 'final_script_log.txt').write_text(
                'step 0 action: params: test\nfinal result: ' + json.dumps({'asin': case['asin']}))
        old = {'candidate': candidate, 'cases': cases,
               'script_sha256': hashlib.sha256(script.read_bytes()).hexdigest()}
        assert pipeline.recheck_saved_attempt('https://www.amazon.sg/', prior, 1, old,
                                               Path(tmp) / 'check.json')['status'] == 'verified'
        # A second resume may point at a script from an earlier run, not an
        # attempt directory under the immediately preceding summary.
        old['script'] = str(script)
        assert pipeline.recheck_saved_attempt('https://www.amazon.sg/', Path(tmp) / 'other', 9, old,
                                               Path(tmp) / 'check_again.json')['status'] == 'verified'
        cases['held']['exit_code'] = 1
        assert pipeline.recheck_saved_attempt('https://www.amazon.sg/', prior, 1, old,
                                               Path(tmp) / 'bad.json')['status'] == 'not_recheckable'
        cases['held']['exit_code'] = 0
        config['agent']['system_template'] = 'held input HELDID'
        (root / 'config.yaml').write_text(yaml.safe_dump(config))
        assert pipeline.recheck_saved_attempt('https://www.amazon.sg/', prior, 1, old,
                                               Path(tmp) / 'leaked.json')['status'] == 'recheck_failed'
