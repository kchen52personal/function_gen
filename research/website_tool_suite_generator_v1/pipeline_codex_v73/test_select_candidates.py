from __future__ import annotations

import json
from pathlib import Path

import pytest

from select_candidates import (
    SelectionError,
    _evidence_url_supported,
    _recon_observations,
    _valid,
    goal_schema_mismatches,
    integer_range_coverage_errors,
    select,
)


PROPOSAL = (
    Path(__file__).resolve().parents[1]
    / "netease_v1"
    / "local_candidates_v1"
    / "proposal.json"
)


def _candidate(index: int) -> dict:
    noun = [
        "artists", "charts", "playlists", "tracks", "metadata",
        "lyrics", "similarity", "podcasts", "albums", "concerts",
    ][index]
    return {
        "name": f"retrieve_{noun}",
        "effect_key": f"{noun}.records",
        "goal_class": f"{noun}_retrieval",
        "output_object_type": f"{noun}_records",
        "choose_when": f"the user requests {noun}",
        "exclude_when": f"the user requests a different object than {noun}",
        "goal": f"Retrieve structured {noun} records",
        "arguments": [{
            "name": "query",
            "type": "str",
            "default": noun,
            "constraints": "non-empty",
        }],
        "test_cases": {
            "default": {"query": noun},
            "held": {"query": noun + " held"},
        },
        "returns": {"name": "str", "metadata": "str"},
        "return_evidence": {
            "name": {"evidence_id": "p00.s00.heading.000"},
            "metadata": {"evidence_id": "p00.s00.heading.000"},
        },
        "boundary": "read-only extraction",
        "success_contract": f"at least one {noun} record is extracted and validated",
        "popularity_proxy": {
            "claim": "high",
            "signals": ["p00.s00.heading.000"],
        },
        "evidence": {
            "page_url": f"https://example.test/{noun}",
            "evidence_id": "p00.s00.heading.000",
        },
        "priority": index + 1,
    }


def test_live_local_proposal_is_rejected_as_navigation_only() -> None:
    proposal = json.loads(PROPOSAL.read_text(encoding="utf-8"))
    with pytest.raises(SelectionError) as captured:
        select(proposal)
    assert captured.value.audit["selected"] == 0
    assert len(captured.value.audit["rejected"]) == 20


def test_navigation_and_url_only_contract_is_invalid() -> None:
    item = _candidate(0)
    item["goal"] = "Navigate to an artist page"
    item["returns"] = {"url": "str"}
    item["return_evidence"] = {"url": {"evidence_id": "p00.s00.heading.000"}}
    item["output_object_type"] = "artist_page_url"
    item["success_contract"] = "The browser navigates to the artist page"
    errors = _valid(item)
    assert any("navigation-only" in error for error in errors)
    assert any("URL-only" in error for error in errors)


def test_explicit_goal_promises_must_fit_flat_return_schema() -> None:
    candidate = _candidate(0)
    candidate['goal'] = 'Retrieve a category name, description, and portal URL'
    candidate['returns'] = {'category_name': 'str', 'category_heading': 'str', 'contents_page_url': 'str'}
    candidate['return_evidence'] = {field: {'evidence_id': 'p00.s00.heading.000'}
                                    for field in candidate['returns']}
    errors = _valid(candidate)
    assert any('description' in error for error in errors)
    assert any('portal URL' in error for error in errors)
    candidate['returns'] = {'category_name': 'str', 'description': 'str', 'portal_url': 'str'}
    candidate['return_evidence'] = {field: {'evidence_id': 'p00.s00.heading.000'}
                                    for field in candidate['returns']}
    assert not goal_schema_mismatches(candidate)
    assert not _valid(candidate)
    # A generic URL or a clearly named summary can carry these promised fields.
    assert not goal_schema_mismatches({**candidate, 'returns': {'summary': 'str', 'url': 'str'}})
    # Do not invent nested-field semantics from an opaque object schema.
    assert not goal_schema_mismatches({**candidate, 'returns': {'record': 'object'}})


def test_explicit_link_goal_requires_url_bearing_flat_field() -> None:
    candidate = _candidate(0)
    candidate['goal'] = 'Retrieve content category links'
    candidate['returns'] = {'categories': 'list[str]'}
    assert any('promises links' in error for error in goal_schema_mismatches(candidate))
    candidate['returns'] = {'category_links': 'list[str]'}
    assert not goal_schema_mismatches(candidate)
    candidate['goal'] = 'Retrieve link text'
    candidate['returns'] = {'text': 'str'}
    assert not goal_schema_mismatches(candidate)
    candidate['goal'] = 'Retrieve category links'
    candidate['returns'] = {'items': 'list[object]'}
    assert not goal_schema_mismatches(candidate)


def test_unspecified_sample_is_screened_before_expensive_exact_comparison() -> None:
    candidate = _candidate(0)
    candidate['goal'] = 'Extract the title and sample content text from a named section'
    candidate['returns'] = {'section_title': 'str', 'content_text': 'str'}
    candidate['success_contract'] = 'Returns non-empty title and content from the section'
    candidate['boundary'] = 'Only extracts visible section text'
    assert any('sample content' in error for error in goal_schema_mismatches(candidate))
    candidate['success_contract'] = 'Returns the first 3 facts in source order'
    assert not goal_schema_mismatches(candidate)
    candidate['success_contract'] = 'Returns the first paragraph of the section'
    assert not goal_schema_mismatches(candidate)


def test_observed_option_examples_are_not_an_exhaustive_domain() -> None:
    candidate = _candidate(0)
    candidate['arguments'] = [{'name': 'subject_filter', 'type': 'str', 'default': 'Culture',
                               'constraints': "must match one of the observed subject headings such as 'Culture'"}]
    assert any('open observed-option domain' in error for error in goal_schema_mismatches(candidate))
    candidate['arguments'][0]['constraints'] = 'must be one of: culture, geography'
    assert not goal_schema_mismatches(candidate)
    candidate['arguments'][0]['constraints'] = 'any nonempty search query'
    assert not goal_schema_mismatches(candidate)


def test_section_heading_is_not_a_metadata_field_type() -> None:
    candidate = _candidate(0)
    candidate['goal'] = 'Retrieve a metadata field value and field type'
    candidate['returns'] = {'field_value': 'str', 'field_type': 'str'}
    candidate['success_contract'] = 'returns requested field value, section heading as field type'
    assert any('unrelated section heading' in error for error in goal_schema_mismatches(candidate))
    candidate['success_contract'] = 'returns requested field value and its field type'
    assert not goal_schema_mismatches(candidate)


def test_internal_heading_evidence_ids_are_not_website_outputs() -> None:
    candidate = _candidate(0)
    candidate['goal'] = 'Retrieve the section heading and evidence ID'
    candidate['returns'] = {'heading_text': 'str', 'heading_evidence_id': 'str'}
    candidate['return_evidence'] = {'heading_text': {'evidence_id': 'p00.s00.heading.001'},
                                    'heading_evidence_id': {'evidence_id': 'p00.s00.heading.001'}}
    assert any('reconnaissance heading evidence ID' in error for error in goal_schema_mismatches(candidate))
    candidate['returns'] = {'heading_text': 'str', 'heading_id': 'str'}
    candidate['return_evidence'] = {'heading_text': {'evidence_id': 'p00.s00.heading.001'},
                                    'heading_id': {'evidence_id': 'p00.s00.heading.001'}}
    assert not goal_schema_mismatches(candidate)


def test_internal_recon_buckets_are_not_page_sections() -> None:
    candidate = _candidate(0)
    candidate['goal'] = 'Retrieve links from the other_links section'
    candidate['returns'] = {'links': 'list[str]'}
    assert any('reconnaissance link bucket' in error for error in goal_schema_mismatches(candidate))
    candidate['returns'] = {'links': 'list[object]'}
    assert any('reconnaissance link bucket' in error for error in goal_schema_mismatches(candidate))
    candidate['goal'] = 'Retrieve links from the Contents section'
    assert not goal_schema_mismatches(candidate)


def test_headline_goal_must_identify_its_text_unit() -> None:
    candidate = _candidate(0)
    candidate['goal'] = 'Retrieve the headline text and associated article URL'
    candidate['returns'] = {'headline_text': 'str', 'headline_url': 'str'}
    candidate['success_contract'] = 'Return a non-empty headline_text and URL'
    candidate['boundary'] = 'Only news items'
    assert any('full item versus link text' in error for error in goal_schema_mismatches(candidate))
    candidate['success_contract'] = 'Return the entire news item and the associated URL'
    assert not goal_schema_mismatches(candidate)
    candidate['success_contract'] = "Return the first link's text and associated URL"
    assert not goal_schema_mismatches(candidate)


def test_bounded_integer_contract_requires_supported_boundary_coverage() -> None:
    item = _candidate(0)
    item['arguments'] = [{'name': 'limit', 'type': 'int', 'default': 3,
                          'constraints': 'must be between 1 and 10'}]
    item['test_cases'] = {'default': {'limit': 3}, 'held': {'limit': 5}}
    assert any('ambiguous bounded integer' in error for error in _valid(item))
    for text in ('1-10', 'from 1 to 10', 'between 1 and 10 exclusive',
                 'between 1 and 10 inclusive except 4', 'between 1 and 1000 inclusive'):
        item['arguments'][0]['constraints'] = text
        assert integer_range_coverage_errors(item), text
    item['arguments'][0]['constraints'] = 'must be between 1 and 10 inclusive'
    assert not integer_range_coverage_errors(item)
    assert not _valid(item)
    item['test_cases']['held']['limit'] = 11
    assert any('outside the declared inclusive range' in error for error in _valid(item))
    item['arguments'][0]['constraints'] = 'positive integer'
    assert not integer_range_coverage_errors(item)


def test_ten_structured_distinct_functions_pass() -> None:
    proposal = {
        "site_id": "example",
        "site_name": "Example",
        "start_url": "https://example.test/",
        "allowed_hosts": ["example.test"],
        "candidates": [_candidate(index) for index in range(10)],
    }
    manifest, audit = select(proposal)
    assert manifest["tool_count"] == 10
    assert audit["status"] == "pass"
    assert audit["selected"] == 10


def test_partial_candidates_can_proceed_without_waiting_for_ten() -> None:
    proposal = {
        "site_id": "example",
        "site_name": "Example",
        "start_url": "https://example.test/",
        "allowed_hosts": ["example.test"],
        "candidates": [_candidate(index) for index in range(7)],
    }

    manifest, audit = select(proposal, require_exact_count=False)

    assert manifest["tool_count"] == 7
    assert [tool["name"] for tool in manifest["tools"]] == [
        _candidate(index)["name"] for index in range(7)
    ]
    assert manifest["status"] == "model_selected_partial_unverified"
    assert audit["status"] == "partial_pass"
    assert audit["selected"] == 7


def test_candidates_beyond_capacity_are_audited() -> None:
    proposal = {
        "site_id": "example",
        "site_name": "Example",
        "start_url": "https://example.test/",
        "allowed_hosts": ["example.test"],
        "candidates": [_candidate(index) for index in range(10)],
    }
    for index, noun in enumerate(("genres", "radios", "videos"), start=10):
        candidate = _candidate(0)
        candidate.update({
            "name": f"retrieve_{noun}",
            "effect_key": f"{noun}.records",
            "goal_class": f"{noun}_retrieval",
            "output_object_type": f"{noun}_records",
            "choose_when": f"the user requests {noun}",
            "exclude_when": f"the user requests a different object than {noun}",
            "goal": f"Retrieve structured {noun} records",
            "returns": {"name": "str", "metadata": "str"},
            "success_contract": f"at least one {noun} record is extracted and validated",
            "priority": index + 1,
            "evidence": {
                "page_url": f"https://example.test/{noun}",
                "evidence_id": "p00.s00.heading.000",
            },
        })
        candidate["arguments"][0]["default"] = noun
        candidate["test_cases"] = {
            "default": {"query": noun},
            "held": {"query": noun + " held"},
        }
        proposal["candidates"].append(candidate)

    manifest, audit = select(proposal)

    assert manifest["tool_count"] == 10
    assert len(audit["rejected"]) == 3
    assert all("capacity" in item["reason"] for item in audit["rejected"])


def test_candidate_requires_two_typed_distinct_cases() -> None:
    item = _candidate(0)
    item.pop("test_cases")
    assert any("test_cases" in error for error in _valid(item))
    item = _candidate(0)
    item["test_cases"]["held"] = dict(item["test_cases"]["default"])
    assert any("must differ" in error for error in _valid(item))
    item = _candidate(0)
    item["test_cases"]["held"]["query"] = 3
    assert any("wrong type" in error for error in _valid(item))


def test_recon_inventory_index_is_not_a_defined_user_collection() -> None:
    from select_candidates import goal_schema_mismatches
    candidate = {'goal': 'Retrieve a specific content sample by content index',
                 'arguments': [{'name': 'content_index', 'type': 'int'}],
                 'returns': {'content_text': 'str'},
                 'proposal_evidence': {'evidence_id': 'p00.s00.content.000'}}
    assert any('recon' in e for e in goal_schema_mismatches(candidate))
    other = {**candidate, 'goal': 'Retrieve a specific other link by index',
             'arguments': [{'name': 'link_index', 'type': 'int'}],
             'proposal_evidence': {'evidence_id': 'p00.s00.link.051'}}
    assert any('recon' in e for e in goal_schema_mismatches(other))
    meaningful = {**other, 'goal': 'Retrieve a primary navigation link by index'}
    assert not any('recon' in e for e in goal_schema_mismatches(meaningful))
    no_evidence = {**candidate, 'proposal_evidence': {}}
    assert not any('recon' in e for e in goal_schema_mismatches(no_evidence))


def test_missing_goal_class_is_audibly_derived_from_effect_key() -> None:
    item = _candidate(0)
    item.pop("goal_class")
    proposal = {"site_id": "example", "site_name": "Example",
                "start_url": "https://example.test/", "allowed_hosts": ["example.test"],
                "candidates": [item]}
    manifest, audit = select(proposal, require_exact_count=False)
    assert manifest["tool_count"] == 1
    assert manifest["tools"][0]["goal_class"] == "derived_artists_records"
    assert audit["normalizations"] == [{
        "name": "retrieve_artists", "field": "goal_class",
        "value": "derived_artists_records", "source": "effect_key"}]


def test_nondeterministic_or_ignored_argument_candidates_fail_closed() -> None:
    item = _candidate(0)
    item.update(name="get_random_record", goal="Fetch a randomly selected record")
    assert any("nondeterministic" in error for error in _valid(item))
    item = _candidate(0)
    item["arguments"][0]["constraints"] = "Ignored by the website"
    assert any("ignored or unused" in error for error in _valid(item))
    item = _candidate(0)
    item["output_object_type"] = "list[record]"
    assert any("no list-valued" in error for error in _valid(item))


def test_each_return_field_requires_captured_evidence() -> None:
    item = _candidate(0)
    item["return_evidence"].pop("metadata")
    errors = _valid(item)
    assert any("metadata" in error and "lack evidence" in error for error in errors)


def test_return_evidence_id_is_checked_against_recon() -> None:
    item = _candidate(0)
    recon = {
        "pages": [{
            "final_url": "https://example.test/artists",
            "title": "Artists",
            "frames": [{
                "url": "https://example.test/artists",
                "headings": [{"text": "artists"}],
            }],
        }],
    }
    assert _valid(item, _recon_observations(recon)) == []
    item["return_evidence"]["metadata"]["evidence_id"] = "p00.s00.heading.999"
    errors = _valid(item, _recon_observations(recon))
    assert any("metadata" in error and "was not observed" in error for error in errors)


def test_evidence_url_accepts_canonical_page_without_tracking_query() -> None:
    observed = {
        "page_url": "https://shop.test/s?node=42&ref=tracking",
        "record": {"text": "Results"},
    }
    assert _evidence_url_supported("https://shop.test/s", observed)
    assert _evidence_url_supported("https://shop.test/s?node=42", observed)
    assert not _evidence_url_supported("https://shop.test/dp/other", observed)


def test_evidence_url_accepts_captured_link_target() -> None:
    observed = {
        "page_url": "https://shop.test/",
        "record": {"href": "/product/123?ref=home"},
    }
    assert _evidence_url_supported("https://shop.test/product/123", observed)
    assert not _evidence_url_supported("https://shop.test/product/456", observed)
