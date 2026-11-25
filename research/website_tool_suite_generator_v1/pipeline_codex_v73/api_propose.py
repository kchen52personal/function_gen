#!/usr/bin/env python3
"""Propose site tools through one bounded OpenAI-compatible API request."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from local_propose import SPEND_LEDGER, _append_jsonl, _extract_json, _prompt


SHARED_ROOT = Path("/shared/hans").resolve()
MAX_API_RESPONSE_BYTES = 4 * 1024 * 1024
API_LOGGER_ROOT = Path(
    "/shared/hans/file upload/api_logger_extracted/api_logger_new"
)


def _inside_shared(path: Path) -> Path:
    resolved = path.resolve()
    if resolved != SHARED_ROOT and SHARED_ROOT not in resolved.parents:
        raise ValueError("API key file and outputs must stay inside /shared/hans")
    return resolved


def _resolve_api_key(key_file: Path | None) -> str:
    """Read a key without accepting it as a command-line argument."""
    from_env = os.environ.get("WEBSITE_TOOL_API_KEY", "").strip()
    if from_env:
        return from_env
    if key_file is None:
        raise ValueError(
            "set WEBSITE_TOOL_API_KEY or pass --api-key-file; never put a key "
            "directly in the command line"
        )
    resolved = _inside_shared(key_file)
    mode = stat.S_IMODE(resolved.stat().st_mode)
    if mode & 0o077:
        raise ValueError("API key file must not be readable or writable by group/others")
    key = resolved.read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError("API key file is empty")
    return key


def _logger(output_path: Path):
    if str(API_LOGGER_ROOT) not in sys.path:
        sys.path.insert(0, str(API_LOGGER_ROOT))
    from local_api_logger import APILogger

    return APILogger(str(output_path.parent / "api_logs"))


def _safe_detail_log(logger: Any, **kwargs: Any) -> str | None:
    """A broken detailed logger must not suppress the spend-ledger record."""
    try:
        logger.log_call(**kwargs)
    except Exception as error:
        return type(error).__name__
    return None


def _usage(response: dict[str, Any]) -> dict[str, int]:
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    input_details = usage.get("prompt_tokens_details")
    if not isinstance(input_details, dict):
        input_details = usage.get("input_tokens_details")
    if not isinstance(input_details, dict):
        input_details = {}
    output_details = usage.get("completion_tokens_details")
    if not isinstance(output_details, dict):
        output_details = usage.get("output_tokens_details")
    if not isinstance(output_details, dict):
        output_details = {}
    input_tokens = int(
        usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
    )
    output_tokens = int(
        usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    )
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": int(input_details.get("cached_tokens", 0) or 0),
        "output_tokens": output_tokens,
        "reasoning_output_tokens": int(
            output_details.get("reasoning_tokens", 0) or 0
        ),
        "total_tokens": int(
            usage.get("total_tokens", input_tokens + output_tokens)
            or input_tokens + output_tokens
        ),
    }


def _failed_usage(response: dict[str, Any]) -> dict[str, int | None]:
    """Keep unreported failure usage unknown rather than assuming it was free."""
    usage = response.get('usage') if isinstance(response.get('usage'), dict) else {}
    def value(mapping: dict, *names: str) -> int | None:
        for name in names:
            number = mapping.get(name)
            if type(number) is int and number >= 0:
                return number
        return None
    input_tokens = value(usage, 'prompt_tokens', 'input_tokens')
    output_tokens = value(usage, 'completion_tokens', 'output_tokens')
    total = value(usage, 'total_tokens')
    if total is None and input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    input_details = usage.get('prompt_tokens_details', usage.get('input_tokens_details', {}))
    output_details = usage.get('completion_tokens_details', usage.get('output_tokens_details', {}))
    return {'input_tokens': input_tokens, 'output_tokens': output_tokens, 'total_tokens': total,
            'cached_input_tokens': value(input_details, 'cached_tokens') if isinstance(input_details, dict) else None,
            'reasoning_output_tokens': value(output_details, 'reasoning_tokens') if isinstance(output_details, dict) else None}


def _model_used(response: dict[str, Any], requested_model: str) -> str:
    returned = str(response.get("model", "")).strip()
    return returned or requested_model


def _cost(
    response: dict[str, Any],
    token_usage: dict[str, int],
    input_usd_per_million: float | None,
    output_usd_per_million: float | None,
) -> tuple[float | None, str]:
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    for container in (usage, response):
        for key in ("cost", "total_cost", "cost_usd", "total_cost_usd"):
            value = container.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                return float(value), f"provider_response.{key}"
    if input_usd_per_million is None or output_usd_per_million is None:
        return None, "monthly_quota_rate_not_exposed"
    calculated = (
        token_usage["input_tokens"] * input_usd_per_million
        + token_usage["output_tokens"] * output_usd_per_million
    ) / 1_000_000
    return calculated, "configured_token_rates"


def _response_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("API response has no choices")
    message = choices[0].get("message", {})
    content = message.get("content", "") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
        )
    raise ValueError("API response message has no text content")


def _post_json(
    endpoint: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("API endpoint must be an HTTPS URL")
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(MAX_API_RESPONSE_BYTES + 1)
            if len(raw) > MAX_API_RESPONSE_BYTES:
                raise ValueError('API response exceeds the 4 MiB limit')
            data = json.loads(raw.decode("utf-8"))
    except HTTPError as error:
        body = error.read(1001).decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"API HTTP {error.code}: {body}") from error
    except URLError as error:
        raise RuntimeError(f"API connection failed: {error.reason}") from error
    if not isinstance(data, dict):
        raise ValueError("API returned a non-object JSON response")
    return data


def propose_api(
    recon_path: Path,
    output_path: Path,
    *,
    endpoint: str,
    model: str,
    key_file: Path | None,
    max_output_tokens: int,
    max_input_chars: int,
    input_usd_per_million: float | None,
    output_usd_per_million: float | None,
    max_usd: float,
    timeout_seconds: int,
    billing_mode: str = "token_rates",
    additional_context: str = "",
) -> dict[str, Any]:
    recon_path = _inside_shared(recon_path)
    output_path = _inside_shared(output_path)
    if not model.strip():
        raise ValueError("model must be non-empty")
    if not 512 <= max_output_tokens <= 8192:
        raise ValueError("max-output-tokens must be between 512 and 8192")
    if not 10_000 <= max_input_chars <= 1_000_000:
        raise ValueError("max-input-chars must be between 10000 and 1000000")
    if billing_mode not in {"token_rates", "monthly_quota"}:
        raise ValueError("billing-mode must be token_rates or monthly_quota")
    if max_usd < 0:
        raise ValueError("max-usd must be non-negative")
    if billing_mode == "token_rates":
        if input_usd_per_million is None or output_usd_per_million is None:
            raise ValueError("token_rates billing requires both token rates")
        if min(input_usd_per_million, output_usd_per_million) < 0:
            raise ValueError("token rates must be non-negative")
    else:
        input_usd_per_million = None
        output_usd_per_million = None
    if not 10 <= timeout_seconds <= 1800:
        raise ValueError("timeout-seconds must be between 10 and 1800")

    recon = json.loads(recon_path.read_text(encoding="utf-8"))
    prompt = _prompt(recon)
    if additional_context:
        if len(additional_context) > 40_000:
            raise ValueError("additional proposal feedback exceeds 40000 characters")
        prompt += "\n\nPrevious pipeline feedback and constraints:\n" + additional_context
    if len(prompt) > max_input_chars:
        raise ValueError(
            f"prompt has {len(prompt)} characters; cap is {max_input_chars}"
        )
    # One Unicode character per token is deliberately conservative for a
    # provider-independent preflight. Actual provider usage is logged below.
    estimated_input_tokens_upper = len(prompt)
    worst_case_usd = None
    if input_usd_per_million is not None and output_usd_per_million is not None:
        worst_case_usd = (
            estimated_input_tokens_upper * input_usd_per_million
            + max_output_tokens * output_usd_per_million
        ) / 1_000_000
    if worst_case_usd is not None and worst_case_usd > max_usd:
        raise ValueError(
            f"worst-case request cost ${worst_case_usd:.6f} exceeds "
            f"--max-usd ${max_usd:.6f}"
        )

    api_key = _resolve_api_key(key_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_output_tokens,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    started = time.monotonic()
    response: dict[str, Any] = {}
    api_logger = _logger(output_path)
    try:
        response = _post_json(endpoint, api_key, payload, timeout_seconds)
        text = _response_text(response)
        result = _extract_json(text)
    except Exception as error:
        duration_ms = (time.monotonic() - started) * 1000
        detail_log_error = _safe_detail_log(api_logger,
            model=model,
            request_data=payload,
            response_data=response or {"usage": {}},
            user="hans_website_tool_pipeline",
            duration_ms=duration_ms,
            success=False,
            error=f"{type(error).__name__}: {str(error)[:1000]}",
        )
        token_usage = _failed_usage(response)
        input_tokens = token_usage["input_tokens"]
        output_tokens = token_usage["output_tokens"]
        cost_usage = {'input_tokens': input_tokens or 0, 'output_tokens': output_tokens or 0}
        rates_known = input_tokens is not None and output_tokens is not None
        actual_usd, cost_source = _cost(
            response,
            cost_usage,
            input_usd_per_million if rates_known else None,
            output_usd_per_million if rates_known else None,
        )
        if actual_usd is None and not rates_known:
            cost_source = 'usage_not_reported_after_failure'
        _append_jsonl(SPEND_LEDGER, {
            "experiment_artifact_path": str(output_path),
            "experiment_stage": "candidate_proposal",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": _model_used(response, model),
            "requested_model": model,
            "provider": urlparse(endpoint).netloc,
            "in": input_tokens,
            "cached_in": token_usage["cached_input_tokens"],
            "out": output_tokens,
            "reasoning_out": token_usage["reasoning_output_tokens"],
            "total_tokens": token_usage["total_tokens"],
            "usd": round(actual_usd, 8) if actual_usd is not None else None,
            "cost_source": cost_source,
            "billing_mode": billing_mode,
            "input_usd_per_million": input_usd_per_million,
            "output_usd_per_million": output_usd_per_million,
            "purpose": "website_function_candidate_proposal",
            "status": "rejected_api_or_parse",
            "usage_source": "provider_response" if rates_known else "incomplete_or_unreported_after_failure",
            "error_type": type(error).__name__,
            "detail_log_error_type": detail_log_error,
        })
        raise

    duration_ms = (time.monotonic() - started) * 1000
    model_used = _model_used(response, model)
    detail_log_error = _safe_detail_log(api_logger,
        model=model_used,
        request_data=payload,
        response_data=response,
        user="hans_website_tool_pipeline",
        duration_ms=duration_ms,
        success=True,
    )
    token_usage = _failed_usage(response)
    input_tokens = token_usage["input_tokens"]
    output_tokens = token_usage["output_tokens"]
    rates_known = input_tokens is not None and output_tokens is not None
    actual_usd, cost_source = _cost(
        response,
        {'input_tokens': input_tokens or 0, 'output_tokens': output_tokens or 0},
        input_usd_per_million if rates_known else None,
        output_usd_per_million if rates_known else None,
    )
    if actual_usd is None and not rates_known:
        cost_source = 'usage_not_reported_after_success'
    raw_output_path = output_path.parent / "raw_api_output.txt"
    generation = {
        "backend": "api_chat_completions",
        "provider_host": urlparse(endpoint).netloc,
        "model": model_used,
        "requested_model": model,
        "input_tokens": input_tokens,
        "cached_input_tokens": token_usage["cached_input_tokens"],
        "output_tokens": output_tokens,
        "reasoning_output_tokens": token_usage["reasoning_output_tokens"],
        "total_tokens": token_usage["total_tokens"],
        "duration_seconds": round(duration_ms / 1000, 3),
        "usd": round(actual_usd, 8) if actual_usd is not None else None,
        "cost_source": cost_source,
        "billing_mode": billing_mode,
        "input_usd_per_million": input_usd_per_million,
        "output_usd_per_million": output_usd_per_million,
        "max_usd": max_usd,
        "request_count": 1,
        "status": "accepted_parse",
        "detail_log_error_type": detail_log_error,
        "usage_source": "provider_response" if rates_known else "incomplete_or_unreported_after_success",
        "raw_output": str(raw_output_path),
    }
    _append_jsonl(SPEND_LEDGER, {
        "experiment_artifact_path": str(output_path),
        "experiment_stage": "candidate_proposal",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model_used,
        "requested_model": model,
        "provider": generation["provider_host"],
        "in": input_tokens,
        "cached_in": token_usage["cached_input_tokens"],
        "out": output_tokens,
        "reasoning_out": token_usage["reasoning_output_tokens"],
        "total_tokens": token_usage["total_tokens"],
        "usd": generation["usd"],
        "cost_source": cost_source,
        "billing_mode": billing_mode,
        "input_usd_per_million": input_usd_per_million,
        "output_usd_per_million": output_usd_per_million,
        "purpose": "website_function_candidate_proposal",
        "status": "accepted_parse",
        "detail_log_error_type": detail_log_error,
        "usage_source": generation["usage_source"],
    })
    # Ledger first: a later failure writing proposal artifacts must not erase
    # the already-incurred request from the accounting record.
    raw_output_path.write_text(text, encoding="utf-8")
    result["generation"] = generation
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("recon", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--max-output-tokens", type=int, default=6144)
    parser.add_argument("--max-input-chars", type=int, default=250_000)
    parser.add_argument(
        "--billing-mode",
        choices=("token_rates", "monthly_quota"),
        default="token_rates",
    )
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    parser.add_argument("--max-usd", type=float, default=0.25)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    result = propose_api(
        args.recon,
        args.output,
        endpoint=args.endpoint,
        model=args.model,
        key_file=args.api_key_file,
        max_output_tokens=args.max_output_tokens,
        max_input_chars=args.max_input_chars,
        input_usd_per_million=args.input_usd_per_million,
        output_usd_per_million=args.output_usd_per_million,
        max_usd=args.max_usd,
        timeout_seconds=args.timeout_seconds,
        billing_mode=args.billing_mode,
    )
    print(json.dumps({
        "candidates": len(result["candidates"]),
        "output": str(args.output),
        "usage": result["generation"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
