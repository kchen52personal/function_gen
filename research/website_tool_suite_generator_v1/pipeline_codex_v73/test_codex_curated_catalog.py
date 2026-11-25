import pytest

import codex_curated_catalog as catalog
from codex_curated_catalog import CATALOG, catalog_entry, list_catalog


def test_catalog_rechecks_original_artifacts_and_preserves_provenance() -> None:
    catalog = list_catalog()
    assert 'not a fresh-run score' in catalog['note']
    assert {item['alias'] for item in catalog['tools']} == set(CATALOG)
    assert all(item['source_summary'] and item['provenance'] for item in catalog['tools'])
    assert any('posthoc' in item['provenance'] for item in catalog['tools'])
    heading = next(item for item in catalog['tools'] if item['alias'] == 'main_page_heading_by_index')
    assert heading['status'] == 'available'
    assert 'nested heading' in heading['provenance']
    assert 'visibility not explicitly filtered' in heading['provenance']
    contents = next(item for item in catalog['tools'] if item['alias'] == 'contents_top_level_heading')
    assert contents['status'] == 'available'
    assert 'posthoc' in contents['provenance'] and 'main-content h2' in contents['provenance']


def test_catalog_does_not_resolve_arbitrary_paths() -> None:
    with pytest.raises(ValueError, match='unknown catalog tool'):
        catalog_entry('../../unknown')


def test_one_unavailable_entry_does_not_hide_other_checked_tools(monkeypatch) -> None:
    original = catalog.select_tool
    def one_missing(summary, name):
        if name == 'browse_contents_format':
            raise ValueError('simulated missing certificate')
        return original(summary, name)
    monkeypatch.setattr(catalog, 'select_tool', one_missing)
    entries = list_catalog()['tools']
    unavailable = [entry for entry in entries if entry['status'] == 'unavailable']
    assert len(unavailable) == 1 and unavailable[0]['alias'] == 'contents_format'
    assert 'simulated missing certificate' in unavailable[0]['reason']
    assert all(entry['status'] == 'available' for entry in entries if entry not in unavailable)
