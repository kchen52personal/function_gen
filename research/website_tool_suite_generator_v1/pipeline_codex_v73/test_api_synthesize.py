import pytest

from api_synthesize import _preflight_budget
from local_synthesize import _tool_recon, _validate_code


def test_api_synthesis_preflights_entire_batch() -> None:
    result = _preflight_budget(
        {"one": "x" * 10_000, "two": "y" * 20_000},
        max_input_chars=25_000,
        max_output_tokens=1_000,
        input_usd_per_million=1.0,
        output_usd_per_million=2.0,
        max_usd_per_tool=0.05,
        max_total_usd=0.08,
    )
    assert result["tools"]["one"]["worst_case_usd"] == pytest.approx(0.012)
    assert result["tools"]["two"]["worst_case_usd"] == pytest.approx(0.022)
    assert result["worst_case_total_usd"] == pytest.approx(0.034)


def test_api_synthesis_rejects_batch_before_spend() -> None:
    with pytest.raises(ValueError, match="total ceiling"):
        _preflight_budget(
            {"one": "x" * 20_000, "two": "y" * 20_000},
            max_input_chars=25_000,
            max_output_tokens=2_000,
            input_usd_per_million=1.0,
            output_usd_per_million=2.0,
            max_usd_per_tool=0.05,
            max_total_usd=0.04,
        )


def test_monthly_quota_preflight_keeps_token_caps_without_fake_dollars() -> None:
    result = _preflight_budget(
        {"one": "x" * 10_000},
        max_input_chars=25_000,
        max_output_tokens=1_000,
        input_usd_per_million=None,
        output_usd_per_million=None,
        max_usd_per_tool=0.05,
        max_total_usd=0.08,
        billing_mode="monthly_quota",
    )
    assert result["billing_mode"] == "monthly_quota"
    assert result["tools"]["one"]["worst_case_usd"] is None
    assert result["worst_case_total_usd"] is None


def test_static_gate_accepts_required_literal_parameter_log() -> None:
    code = '''
import argparse
from playwright.async_api import async_playwright
async def sample_tool(query: str):
    print("step 0 action: params:", query)
    print("final result: ")
    await async_playwright().start()
    await playwright.firefox.launch()
    viewport = 1280
    screenshot = True
'''
    assert _validate_code(code, "sample_tool") == []


def test_static_gate_accepts_webwright_native_chromium() -> None:
    code = '''
import argparse
from playwright.async_api import async_playwright
async def sample_tool(query: str):
    print("step 0 action: params:", query)
    print("final result: ")
    await async_playwright().start()
    await playwright.chromium.launch()
    viewport = 1280
    screenshot = True
'''
    assert _validate_code(code, "sample_tool") == []


def test_static_gate_accepts_playwright_context_alias() -> None:
    code = '''
import argparse
from playwright.async_api import async_playwright
async def sample_tool(query: str):
    print("step 0 action: params:", query)
    print("final result: ")
    async with async_playwright() as p:
        await p.chromium.launch(headless=True)
    viewport = 1280
    screenshot = True
'''
    assert _validate_code(code, "sample_tool") == []


def test_static_gate_rejects_page_wide_result_fallback() -> None:
    code = '''
import argparse
from playwright.async_api import async_playwright
async def sample_tool(query: str):
    print("step 0 action: params:", query)
    print("final result: ")
    await async_playwright().start()
    await playwright.firefox.launch()
    viewport = 1280
    screenshot = True
    return await page.query_selector("body")
'''
    errors = _validate_code(code, "sample_tool")
    assert "uses page-wide fallback selector 'body'" in errors


def test_static_gate_allows_specific_result_container_children() -> None:
    code = '''
import argparse
from playwright.async_api import async_playwright
async def sample_tool(query: str):
    print("step 0 action: params:", query)
    print("final result: ")
    await async_playwright().start()
    await playwright.firefox.launch()
    viewport = 1280
    screenshot = True
    container = await page.query_selector("#search-results")
    return await container.query_selector_all("li")
'''
    assert _validate_code(code, "sample_tool") == []


def test_static_gate_rejects_union_with_global_list_fallback() -> None:
    code = '''
import argparse
from playwright.async_api import async_playwright
async def sample_tool(query: str):
    print("step 0 action: params:", query)
    print("final result: ")
    await async_playwright().start()
    playwright.firefox.launch
    viewport = 1280
    screenshot = True
    return await page.wait_for_selector("#search-results, ul li")
'''
    errors = _validate_code(code, "sample_tool")
    assert "uses broad union fallback selector '#search-results, ul li'" in errors


def test_static_gate_rejects_string_method_before_await() -> None:
    code = '''
import argparse
from playwright.async_api import async_playwright
async def sample_tool(query: str):
    print("step 0 action: params:", query)
    print("final result: ")
    async with async_playwright() as p:
        await p.chromium.launch()
        value = await locator.inner_text().strip()
    viewport = 1280
    screenshot = True
'''
    errors = _validate_code(code, "sample_tool")
    assert "calls strip before awaiting async inner_text" in errors


def test_static_gate_rejects_unawaited_locator_all() -> None:
    code = '''
import argparse
from playwright.async_api import async_playwright
async def sample_tool(query: str):
    print("step 0 action: params:", query)
    print("final result: ")
    async with async_playwright() as p:
        await p.chromium.launch()
        rows = locator.all()
    viewport = 1280
    screenshot = True
'''
    errors = _validate_code(code, "sample_tool")
    assert "calls async Locator.all without await" in errors


def test_static_gate_rejects_nonexistent_page_urljoin() -> None:
    code = '''
import argparse
from playwright.async_api import async_playwright
async def sample_tool(query: str):
    print("step 0 action: params:", query)
    print("final result: ")
    async with async_playwright() as p:
        await p.chromium.launch()
        result = page.urljoin("/item")
    viewport = 1280
    screenshot = True
'''
    errors = _validate_code(code, "sample_tool")
    assert "uses nonexistent page.urljoin" in errors


def test_tool_recon_keeps_only_cited_pages() -> None:
    frame = {
        "url": "https://example.test/",
        "headings": [{"text": "Heading"}],
        "links": [],
        "actions": [],
        "fields": [],
        "forms": [],
        "content": [],
    }
    recon = {
        "start_url": "https://example.test/",
        "allowed_host": "example.test",
        "pages": [
            {"final_url": "https://example.test/one", "frames": [frame]},
            {"final_url": "https://example.test/two", "frames": [frame]},
        ],
        "repeated_first_party_routes": [],
    }
    tool = {
        "name": "sample_tool",
        "proposal_evidence": {"evidence_id": "p01.s00.heading.000"},
        "proposal_return_evidence": {},
        "popularity_proxy": {"signals": ["p01.s00.heading.000"]},
    }
    compact = _tool_recon(tool, recon)
    assert compact["selected_page_indices"] == [1]
    assert compact["omitted_page_count"] == 1
    assert [page["url"] for page in compact["pages"]] == ["https://example.test/two"]
