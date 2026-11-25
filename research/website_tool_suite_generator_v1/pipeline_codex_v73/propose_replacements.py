#!/usr/bin/env python3
"""Propose failure-aware replacement intents with one bounded API call."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlparse

from api_propose import (
    _cost,
    _logger,
    _model_used,
    _post_json,
    _resolve_api_key,
    _response_text,
    _usage,
)
from local_propose import SPEND_LEDGER, _append_jsonl, _extract_json


SHARED_ROOT = Path("/shared/hans").resolve()


def inside_shared(path: Path) -> Path:
    resolved = path.resolve()
    if resolved != SHARED_ROOT and SHARED_ROOT not in resolved.parents:
        raise ValueError("state and output paths must stay inside /shared/hans")
    return resolved


def build_prompt(state: dict[str, Any], count: int) -> str:
    accepted = [
        {"name": item["name"], "goal": item["goal"]}
        for item in state.get("accepted", [])
    ]
    failures = [
        {"candidate": item["candidate"], "reason": item["reason"]}
        for item in state.get("failed_attempts", [])
    ]
    screened = [
        {"name": item["name"], "reason": item["reason"]}
        for item in state.get("screened_out", [])
    ]
    return f"""Return one JSON object with key candidates and exactly {count}
replacement user intents for a website-to-code-tool pipeline.

Public site: {state['start_url']}
Description: {state['description']}

Already accepted tools; do not duplicate or create fixed-value subsets:
{json.dumps(accepted, ensure_ascii=False, indent=2)}

Measured failed attempts; propose different functionality, not cosmetic rewrites:
{json.dumps(failures, ensure_ascii=False, indent=2)}

Screened out; do not reintroduce them:
{json.dumps(screened, ensure_ascii=False, indent=2)}

Each candidate must be a common, distinct, public, read-only Amazon.sg user
functionality that a Playwright browser tool can complete without login,
purchase, cart mutation, posting, account access, CAPTCHA bypass or hidden API.
Favor functions with an observable result container and parameters that can be
tested on one default and one held value. Do not split one functionality into
country/category/query variants. Do not assume that producing code proves the
tool works.

Each candidate object must have:
- name: snake_case action name
- user_intent: one concrete sentence
- popularity_reason: why ordinary users commonly do it
- distinct_from_accepted: exact functional boundary
- parameters: object mapping parameter name to a short type/constraint
- expected_result: observable returned records or fact
- likely_public_surface: public page/route or visible control
- default_test: parameter object
- held_test: different parameter object

Rank strongest candidates first. Output JSON only.
"""


def validate_candidates(value: Any, count: int) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("candidates"), list):
        raise ValueError("response must contain a candidates list")
    candidates = value["candidates"]
    if len(candidates) != count:
        raise ValueError(f"expected {count} candidates, got {len(candidates)}")
    required = {
        "name", "user_intent", "popularity_reason", "distinct_from_accepted",
        "parameters", "expected_result", "likely_public_surface",
        "default_test", "held_test",
    }
    names: set[str] = set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict) or not required.issubset(candidate):
            raise ValueError(f"candidate {index} is missing required fields")
        name = candidate["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(f"candidate {index} has invalid name")
        if name in names:
            raise ValueError(f"duplicate candidate name: {name}")
        names.add(name)
        if not all(isinstance(candidate[key], dict) for key in ("parameters", "default_test", "held_test")):
            raise ValueError(f"candidate {name} has invalid parameter/test objects")
        if candidate["default_test"] == candidate["held_test"]:
            raise ValueError(f"candidate {name} has identical default and held tests")
    return candidates


def propose(state_path: Path, output_path: Path, endpoint: str, model: str,
            key_file: Path, count: int, max_tokens: int, timeout: int) -> dict[str, Any]:
    state_path = inside_shared(state_path)
    output_path = inside_shared(output_path)
    if not 5 <= count <= 20:
        raise ValueError("count must be between 5 and 20")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    prompt = build_prompt(state, count)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    key = _resolve_api_key(key_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger = _logger(output_path)
    started = time.monotonic()
    response: dict[str, Any] = {}
    try:
        response = _post_json(endpoint, key, payload, timeout)
        raw = _response_text(response)
        parsed = _extract_json(raw)
        candidates = validate_candidates(parsed, count)
        success = True
        error_text = None
    except Exception as error:
        success = False
        error_text = f"{type(error).__name__}: {str(error)[:1000]}"
        candidates = []
        raw = ""
    duration_ms = (time.monotonic() - started) * 1000
    usage = _usage(response)
    model_used = _model_used(response, model)
    logger.log_call(
        model=model_used,
        request_data=payload,
        response_data=response or {"usage": {}},
        user="hans_website_tool_pipeline",
        duration_ms=duration_ms,
        success=success,
        error=error_text,
    )
    usd, cost_source = _cost(response, usage, None, None)
    ledger = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model_used,
        "requested_model": model,
        "provider": urlparse(endpoint).netloc,
        "in": usage["input_tokens"],
        "cached_in": usage["cached_input_tokens"],
        "out": usage["output_tokens"],
        "reasoning_out": usage["reasoning_output_tokens"],
        "total_tokens": usage["total_tokens"],
        "usd": usd,
        "cost_source": cost_source,
        "billing_mode": "monthly_quota",
        "purpose": "website_function_replacement_proposal",
        "status": "accepted_parse" if success else "rejected_api_or_parse",
    }
    _append_jsonl(SPEND_LEDGER, ledger)
    if not success:
        raise RuntimeError(error_text)
    raw_path = output_path.parent / "raw_api_output.txt"
    raw_path.write_text(raw, encoding="utf-8")
    result = {
        "site_id": state.get("site_id"),
        "round": 2,
        "candidates": candidates,
        "generation": {
            **ledger,
            "duration_seconds": round(duration_ms / 1000, 3),
            "request_count": 1,
            "raw_output": str(raw_path),
        },
    }
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("state", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--endpoint", default="https://yibuapi.com/v1/chat/completions")
    parser.add_argument("--model", default="qwen3.5-flash")
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=6144)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    result = propose(
        args.state, args.output, args.endpoint, args.model, args.api_key_file,
        args.count, args.max_tokens, args.timeout,
    )
    print(json.dumps({"status": "ok", "candidates": len(result["candidates"]), "generation": result["generation"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
