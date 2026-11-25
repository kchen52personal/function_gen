#!/usr/bin/env python3
"""Invoke a clearly labeled, manually curated collection of prior experiments.

This is not a fresh suite run or a benchmark score. Each entry is selected via
its original certificate and script hash by the existing invocation wrapper.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from invoke_verified_tool import ROOT, invoke, select_tool


# Aliases make distinct operations with the same generated name addressable.
# Each tuple is (original summary, generated tool name, provenance label).
CATALOG = {
    'contents_format': ('codex_v27_20261109_fresh_suite/summary.json', 'browse_contents_format', 'v27 fresh suite'),
    'content_quality_link': ('codex_v43_20261109_continue_v35/summary.json', 'get_content_quality_link', 'v43 continuation; both allowed options checked'),
    'wikipedia_statistics': ('codex_v27_20261109_fresh_suite/summary.json', 'get_wikipedia_statistics', 'v27 fresh suite'),
    'featured_article_detail': ('codex_v27_20261109_fresh_suite/summary.json', 'get_featured_article_detail', 'v27 fresh suite'),
    'featured_section_heading': ('codex_v33_20261109_fresh_suite/summary.json', 'get_featured_section_heading', 'v33 fresh suite; additional option audit'),
    'main_page_heading_by_index': ('codex_v51_20261109_cheap_continue_v43/summary.json', 'get_main_page_section_header', 'v51 continuation; heading-element order includes nested heading; visibility not explicitly filtered'),
    'contents_top_level_heading': ('codex_v70_20261110_contents_scope_probe/summary_codex_posthoc.json', 'get_contents_section_header', 'posthoc scope clarification and reference retry; main-content h2 only; visibility not explicitly filtered'),
    'news_item_by_position': ('codex_v50_20261109_news_missing_list_repair/summary_codex_posthoc.json', 'get_news_headline_by_position', 'failure-informed, posthoc repair; regression fixtures disclosed'),
    'subject_category': ('codex_v24_20261109_subject_recheck/summary_codex_posthoc.json', 'get_subject_category', 'posthoc grounding recheck'),
    'list_subject_categories': ('codex_v13_20261109_matched/summary.json', 'browse_subject_categories', 'v13 matched-input replay'),
    'trivia_fact_with_link': ('codex_v34_20261109_fact_reference_retry/summary_codex_posthoc.json', 'get_did_you_know_facts', 'posthoc reference retry'),
    'trivia_fact_list': ('codex_v29_20261109_list_probe/summary_codex_posthoc.json', 'get_did_you_know_facts', 'failure-informed posthoc list probe'),
}


def catalog_entry(alias: str) -> tuple[Path, str, str]:
    if alias not in CATALOG:
        raise ValueError('unknown catalog tool')
    path, name, provenance = CATALOG[alias]
    return ROOT / 'automated_runs' / path, name, provenance


def list_catalog() -> dict:
    entries = []
    for alias in CATALOG:
        summary, name, provenance = catalog_entry(alias)
        try:
            candidate = select_tool(summary, name)['candidate']
            entry = {'alias': alias, 'generated_name': name, 'status': 'available', 'goal': candidate['goal'],
                     'arguments': candidate['arguments'], 'returns': candidate['returns'],
                     'source_summary': str(summary), 'provenance': provenance}
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            entries.append({'alias': alias, 'generated_name': name, 'status': 'unavailable',
                            'reason': f'{type(error).__name__}: {str(error)[:300]}',
                            'source_summary': str(summary), 'provenance': provenance})
            continue
        entries.append(entry)
    return {'note': 'Curated across separate experiments; not a fresh-run score. '
                    'Posthoc checks are not unbiased evaluations. Certificates cover '
                    'recorded cases only, and the live site can change.', 'tools': entries}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', action='store_true', help='list checked catalog entries')
    parser.add_argument('--tool', help='catalog alias')
    parser.add_argument('--arguments', help='JSON object of arguments')
    args = parser.parse_args()
    try:
        if args.list and not args.tool and not args.arguments:
            result = list_catalog()
        elif not args.list and args.tool and args.arguments:
            summary, name, provenance = catalog_entry(args.tool)
            result = invoke(summary, name, json.loads(args.arguments))
            result.update({'catalog_alias': args.tool, 'provenance': provenance,
                           'source_summary': str(summary)})
        else:
            parser.error('use --list alone or --tool ALIAS --arguments JSON')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired, RuntimeError) as error:
        print(f'catalog failed: {type(error).__name__}: {error}', file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
