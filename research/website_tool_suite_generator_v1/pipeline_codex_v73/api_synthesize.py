#!/usr/bin/env python3
"""Generate statically gated Playwright tool drafts through a bounded API batch."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlparse

from api_propose import (
    _cost,
    _inside_shared,
    _logger,
    _model_used,
    _post_json,
    _resolve_api_key,
    _response_text,
    _usage,
)
from local_propose import SPEND_LEDGER, _append_jsonl
from local_synthesize import _extract_code, _prompt, _validate_code


def _preflight_budget(
    prompts: dict[str, str],
    *,
    max_input_chars: int,
    max_output_tokens: int,
    input_usd_per_million: float | None,
    output_usd_per_million: float | None,
    max_usd_per_tool: float,
    max_total_usd: float,
    billing_mode: str = "token_rates",
) -> dict[str, Any]:
    if not 512 <= max_output_tokens <= 16_384:
        raise ValueError("max-output-tokens must be between 512 and 16384")
    if not 10_000 <= max_input_chars <= 1_000_000:
        raise ValueError("max-input-chars must be between 10000 and 1000000")
    if billing_mode not in {"token_rates", "monthly_quota"}:
        raise ValueError("billing-mode must be token_rates or monthly_quota")
    if min(max_usd_per_tool, max_total_usd) < 0:
        raise ValueError("dollar ceilings must be non-negative")
    if billing_mode == "token_rates":
        if input_usd_per_million is None or output_usd_per_million is None:
            raise ValueError("token_rates billing requires both token rates")
        if min(input_usd_per_million, output_usd_per_million) < 0:
            raise ValueError("token rates must be non-negative")
    else:
        input_usd_per_million = None
        output_usd_per_million = None

    tools: dict[str, dict[str, Any]] = {}
    total: float | None = 0.0 if billing_mode == "token_rates" else None
    for tool_name, prompt in prompts.items():
        chars = len(prompt)
        if chars > max_input_chars:
            raise ValueError(
                f"{tool_name} prompt has {chars} characters; cap is {max_input_chars}"
            )
        # One character per token is intentionally conservative and independent
        # of a provider tokenizer. Provider-reported exact usage is logged later.
        worst_usd = None
        if input_usd_per_million is not None and output_usd_per_million is not None:
            worst_usd = (
                chars * input_usd_per_million
                + max_output_tokens * output_usd_per_million
            ) / 1_000_000
        if worst_usd is not None and worst_usd > max_usd_per_tool:
            raise ValueError(
                f"{tool_name} worst-case cost ${worst_usd:.6f} exceeds "
                f"per-tool ceiling ${max_usd_per_tool:.6f}"
            )
        tools[tool_name] = {
            "input_characters": chars,
            "conservative_input_token_upper_bound": chars,
            "worst_case_usd": round(worst_usd, 8) if worst_usd is not None else None,
        }
        if total is not None and worst_usd is not None:
            total += worst_usd
    if total is not None and total > max_total_usd:
        raise ValueError(
            f"batch worst-case cost ${total:.6f} exceeds total ceiling "
            f"${max_total_usd:.6f}"
        )
    return {
        "tools": tools,
        "billing_mode": billing_mode,
        "worst_case_total_usd": round(total, 8) if total is not None else None,
        "max_total_usd": max_total_usd,
    }


def _ledger_record(
    *,
    endpoint: str,
    requested_model: str,
    response: dict[str, Any],
    token_usage: dict[str, int],
    usd: float | None,
    cost_source: str,
    input_usd_per_million: float | None,
    output_usd_per_million: float | None,
    billing_mode: str,
    tool_name: str,
    status: str,
    error_type: str | None = None,
) -> dict[str, Any]:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": _model_used(response, requested_model),
        "requested_model": requested_model,
        "provider": urlparse(endpoint).netloc,
        "in": token_usage["input_tokens"],
        "cached_in": token_usage["cached_input_tokens"],
        "out": token_usage["output_tokens"],
        "reasoning_out": token_usage["reasoning_output_tokens"],
        "total_tokens": token_usage["total_tokens"],
        "usd": round(usd, 8) if usd is not None else None,
        "cost_source": cost_source,
        "billing_mode": billing_mode,
        "input_usd_per_million": input_usd_per_million,
        "output_usd_per_million": output_usd_per_million,
        "purpose": "website_tool_code_synthesis",
        "tool": tool_name,
        "status": status,
    }
    if error_type:
        record["error_type"] = error_type
    return record


def synthesize_api(
    manifest_path: Path,
    recon_path: Path,
    output_root: Path,
    tool_names: list[str],
    *,
    endpoint: str,
    model: str,
    key_file: Path | None,
    max_output_tokens: int,
    max_input_chars: int,
    input_usd_per_million: float | None,
    output_usd_per_million: float | None,
    max_usd_per_tool: float,
    max_total_usd: float,
    timeout_seconds: int,
    billing_mode: str = "token_rates",
) -> dict[str, Any]:
    manifest_path = _inside_shared(manifest_path)
    recon_path = _inside_shared(recon_path)
    output_root = _inside_shared(output_root)
    if not model.strip():
        raise ValueError("model must be non-empty")
    if not 10 <= timeout_seconds <= 1800:
        raise ValueError("timeout-seconds must be between 10 and 1800")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recon = json.loads(recon_path.read_text(encoding="utf-8"))
    tools = {tool["name"]: tool for tool in manifest["tools"]}
    if len(tool_names) != len(set(tool_names)):
        raise ValueError("tool list contains duplicates")
    unknown = sorted(set(tool_names) - set(tools))
    if unknown:
        raise ValueError(f"unknown tools: {', '.join(unknown)}")
    prompts = {
        name: _prompt(manifest, tools[name], recon)
        for name in tool_names
    }
    preflight = _preflight_budget(
        prompts,
        max_input_chars=max_input_chars,
        max_output_tokens=max_output_tokens,
        input_usd_per_million=input_usd_per_million,
        output_usd_per_million=output_usd_per_million,
        max_usd_per_tool=max_usd_per_tool,
        max_total_usd=max_total_usd,
        billing_mode=billing_mode,
    )
    api_key = _resolve_api_key(key_file)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "api_synthesis_preflight.json").write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    summary: dict[str, Any] = {
        "status": "running",
        "requested_model": model,
        "provider_host": urlparse(endpoint).netloc,
        "requested_tools": tool_names,
        "accepted_drafts": [],
        "rejected_drafts": [],
        "usage": [],
        "actual_total_usd": 0.0 if billing_mode == "token_rates" else None,
        "preflight": preflight,
    }
    api_logger = _logger(output_root / "api_synthesis.json")

    for tool_name in tool_names:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompts[tool_name]}],
            "max_tokens": max_output_tokens,
            "temperature": 0,
            "stream": False,
        }
        started = time.monotonic()
        response: dict[str, Any] = {}
        try:
            response = _post_json(endpoint, api_key, payload, timeout_seconds)
            decoded = _response_text(response)
        except Exception as error:
            duration_ms = (time.monotonic() - started) * 1000
            api_logger.log_call(
                model=model,
                request_data=payload,
                response_data=response or {"usage": {}},
                user="hans_website_tool_pipeline",
                duration_ms=duration_ms,
                success=False,
                error=f"{type(error).__name__}: {str(error)[:1000]}",
            )
            token_usage = _usage(response)
            usd, cost_source = _cost(
                response,
                token_usage,
                input_usd_per_million,
                output_usd_per_million,
            )
            _append_jsonl(SPEND_LEDGER, _ledger_record(
                endpoint=endpoint,
                requested_model=model,
                response=response,
                token_usage=token_usage,
                usd=usd,
                cost_source=cost_source,
                input_usd_per_million=input_usd_per_million,
                output_usd_per_million=output_usd_per_million,
                billing_mode=billing_mode,
                tool_name=tool_name,
                status="rejected_api",
                error_type=type(error).__name__,
            ))
            summary["status"] = "stopped_api_failure"
            summary["stopped_on_tool"] = tool_name
            summary["error"] = f"{type(error).__name__}: {str(error)[:1000]}"
            (output_root / "api_synthesis_summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            raise

        duration_ms = (time.monotonic() - started) * 1000
        model_used = _model_used(response, model)
        api_logger.log_call(
            model=model_used,
            request_data=payload,
            response_data=response,
            user="hans_website_tool_pipeline",
            duration_ms=duration_ms,
            success=True,
        )
        token_usage = _usage(response)
        usd, cost_source = _cost(
            response,
            token_usage,
            input_usd_per_million,
            output_usd_per_million,
        )
        if usd is not None:
            summary["actual_total_usd"] = round(
                float(summary["actual_total_usd"] or 0.0) + usd,
                8,
            )
        if (
            summary["actual_total_usd"] is not None
            and summary["actual_total_usd"] > max_total_usd
        ):
            status = "stopped_actual_cost_ceiling"
        else:
            status = "received"

        draft_dir = output_root / "tools" / tool_name / "drafts" / "attempt_1"
        draft_dir.mkdir(parents=True, exist_ok=True)
        (draft_dir / "raw_api_output.txt").write_text(decoded, encoding="utf-8")
        try:
            code = _extract_code(decoded)
            errors = _validate_code(code, tool_name)
        except ValueError as error:
            code, errors = "", [str(error)]
        if errors:
            draft_status = "rejected_static"
            summary["rejected_drafts"].append({
                "tool": tool_name,
                "errors": errors,
            })
        else:
            draft_status = "accepted_for_live_test"
            (draft_dir / "draft_script.py").write_text(code, encoding="utf-8")
            summary["accepted_drafts"].append(tool_name)

        usage_record = {
            "tool": tool_name,
            "requested_model": model,
            "model": model_used,
            **token_usage,
            "duration_seconds": round(duration_ms / 1000, 3),
            "usd": round(usd, 8) if usd is not None else None,
            "cost_source": cost_source,
            "status": draft_status,
        }
        summary["usage"].append(usage_record)
        _append_jsonl(SPEND_LEDGER, _ledger_record(
            endpoint=endpoint,
            requested_model=model,
            response=response,
            token_usage=token_usage,
            usd=usd,
            cost_source=cost_source,
            input_usd_per_million=input_usd_per_million,
            output_usd_per_million=output_usd_per_million,
            billing_mode=billing_mode,
            tool_name=tool_name,
            status=draft_status,
        ))
        (draft_dir / "static_gate.json").write_text(
            json.dumps({
                "status": draft_status,
                "errors": errors,
                "usage": usage_record,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if status == "stopped_actual_cost_ceiling":
            summary["status"] = status
            summary["stopped_on_tool"] = tool_name
            break

    if summary["status"] == "running":
        summary["status"] = "complete_static_gate"
    (output_root / "api_synthesis_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("recon", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--tool", action="append", dest="tools")
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--max-input-chars", type=int, default=250_000)
    parser.add_argument(
        "--billing-mode",
        choices=("token_rates", "monthly_quota"),
        default="token_rates",
    )
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    parser.add_argument("--max-usd-per-tool", type=float, default=0.05)
    parser.add_argument("--max-total-usd", type=float, default=0.50)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    names = args.tools or [tool["name"] for tool in manifest["tools"]]
    result = synthesize_api(
        args.manifest,
        args.recon,
        args.output_root,
        names,
        endpoint=args.endpoint,
        model=args.model,
        key_file=args.api_key_file,
        max_output_tokens=args.max_output_tokens,
        max_input_chars=args.max_input_chars,
        input_usd_per_million=args.input_usd_per_million,
        output_usd_per_million=args.output_usd_per_million,
        max_usd_per_tool=args.max_usd_per_tool,
        max_total_usd=args.max_total_usd,
        timeout_seconds=args.timeout_seconds,
        billing_mode=args.billing_mode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
