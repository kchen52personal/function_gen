#!/usr/bin/env python3
"""Generate WebWright-style draft tools with a local vision-language model."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Any

from local_propose import (
    _append_jsonl,
    _compact_recon,
    _load_bounded_images,
    _select_image_paths,
    load_local_model,
    require_scoped_gpu_approval,
)


DEFAULT_MODEL = Path("/shared/hans/models/OpenWebRL-4B-SFT")
SPEND_LEDGER = Path("/shared/hans/webarena/spend_ledger.jsonl")


def _tool_recon(tool: dict[str, Any], recon: dict[str, Any]) -> dict[str, Any]:
    """Keep only crawl pages cited by this tool while preserving evidence IDs."""
    compact = _compact_recon(recon)
    evidence_ids: set[str] = set()
    proposal_evidence = tool.get("proposal_evidence", {})
    if proposal_evidence.get("evidence_id"):
        evidence_ids.add(str(proposal_evidence["evidence_id"]))
    for item in tool.get("proposal_return_evidence", {}).values():
        if isinstance(item, dict) and item.get("evidence_id"):
            evidence_ids.add(str(item["evidence_id"]))
    popularity = tool.get("popularity_proxy", {})
    for item in popularity.get("signals", []):
        evidence_ids.add(str(item))

    page_indices = sorted({
        int(match.group(1))
        for evidence_id in evidence_ids
        if (match := re.fullmatch(r"p(\d+)\.s\d+\.[a-z]+\.\d+", evidence_id))
    })
    if not page_indices:
        raise ValueError(f"{tool.get('name', 'tool')} has no resolvable evidence pages")
    pages = compact["pages"]
    if any(index >= len(pages) for index in page_indices):
        raise ValueError(f"{tool.get('name', 'tool')} cites an out-of-range page")
    compact["pages"] = [pages[index] for index in page_indices]
    compact["selected_page_indices"] = page_indices
    compact["omitted_page_count"] = len(pages) - len(page_indices)
    return compact


def _prompt(manifest: dict[str, Any], tool: dict[str, Any], recon: dict[str, Any]) -> str:
    return f"""Write one standalone Python Playwright Firefox CLI tool.
Return only one ```python code fence.

Site:
{json.dumps({
    "site_name": manifest["site_name"],
    "start_url": manifest["start_url"],
    "allowed_hosts": manifest["allowed_hosts"],
}, ensure_ascii=False, indent=2)}

Exact tool contract:
{json.dumps(tool, ensure_ascii=False, indent=2)}

Reconnaissance evidence:
{json.dumps(_tool_recon(tool, recon), ensure_ascii=False, indent=2)}

Hard requirements:
- define exactly one reusable async function named {tool["name"]};
- give it typed arguments and a Google-style docstring;
- expose those arguments with argparse and contract defaults;
- open the start URL in a fresh headless Playwright Firefox context;
- set viewport exactly 1280x1800;
- apply argument values through visible controls or a verified first-party route;
- prove that each argument affected the intended control/route before extracting output;
- verify the observable success contract and fail closed with RuntimeError;
- extract returned facts from the live page; do not embed expected answers;
- extract only from a task-specific result container. Never fall back to body,
  the first ul, bare li/a elements, navigation chrome, or arbitrary page text;
- reject skip links, headers, footers, menus and unrelated recommendation blocks;
- every returned record must be traceable to an element inside that result
  container, and record URLs must come from the same element when promised;
- if the task-specific container or promised fields are absent, raise
  RuntimeError instead of returning a weaker substitute;
- save at least two bounded screenshots, never full_page=True;
- write final_script_log.txt in the script directory;
- the first log line must start "step 0 action: params:";
- the final log line must start "final result: " followed by JSON;
- print the same structured JSON result to stdout;
- use timeouts and close browser/context in finally;
- read-only only: no login, posting, purchase, deletion or account mutation;
- no requests/selenium/browser-use libraries and no API calls.
"""


def _extract_code(text: str) -> str:
    fenced = re.search(r"```python\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        return fenced.group(1).strip() + "\n"
    start = text.find("#!/usr/bin/env python")
    if start >= 0:
        return text[start:].strip() + "\n"
    raise ValueError("model output did not contain a Python program")


def _validate_code(code: str, function_name: str) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return [f"syntax error: {error}"]
    async_functions = [
        node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
    ]
    if function_name not in async_functions:
        errors.append(f"missing async function {function_name}")
    required_fragments = {
        "viewport": "1280",
        "parameter log": "step 0 action: params:",
        "final result": "final result: ",
        "argparse": "argparse",
        "screenshots": "screenshot",
    }
    for label, fragment in required_fragments.items():
        if fragment not in code:
            errors.append(f"missing {label}")
    browser_launch = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "launch"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr in {"firefox", "chromium"}
        for node in ast.walk(tree)
    )
    if not browser_launch:
        errors.append("missing Playwright browser launch")
    if "full_page=True" in code or "full_page = True" in code:
        errors.append("uses prohibited full-page screenshot")
    prohibited = ("requests.", "selenium", "os.system(", "subprocess.")
    for fragment in prohibited:
        if fragment in code:
            errors.append(f"uses prohibited fragment {fragment!r}")

    # Common async-Playwright generation errors can compile successfully but
    # always fail at runtime. Reject them before spending a browser attempt.
    async_playwright_methods = {
        "all", "all_inner_texts", "all_text_contents", "click", "count",
        "fill", "get_attribute", "inner_text", "input_value", "press",
        "select_option", "screenshot", "wait_for", "wait_for_selector",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr in {"strip", "lower", "casefold"}:
            receiver = node.func.value
            if (
                isinstance(receiver, ast.Call)
                and isinstance(receiver.func, ast.Attribute)
                and receiver.func.attr in async_playwright_methods
            ):
                errors.append(
                    f"calls {node.func.attr} before awaiting async "
                    f"{receiver.func.attr}"
                )
        if node.func.attr == "urljoin":
            receiver = node.func.value
            if isinstance(receiver, ast.Name) and receiver.id == "page":
                errors.append("uses nonexistent page.urljoin")
        if node.func.attr == "all":
            parent_is_await = any(
                isinstance(parent, ast.Await) and parent.value is node
                for parent in ast.walk(tree)
            )
            if not parent_is_await:
                errors.append("calls async Locator.all without await")

    # A generated extractor must fail closed when its task-specific container
    # is absent; page-wide fallbacks can accidentally select navigation chrome.
    page_wide_selectors = {"body", "html", "ul", "ol", "li", "a", "*", "ul li", "ol li"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        if not isinstance(function, ast.Attribute):
            continue
        if function.attr not in {
            "query_selector", "query_selector_all", "wait_for_selector",
            "locator", "eval_on_selector", "eval_on_selector_all",
        }:
            continue
        selector_node = node.args[0]
        if not isinstance(selector_node, ast.Constant) or not isinstance(selector_node.value, str):
            continue
        selector = " ".join(selector_node.value.casefold().split())
        receiver = function.value
        receiver_name = receiver.id if isinstance(receiver, ast.Name) else ""
        if receiver_name == "page" and selector in page_wide_selectors:
            errors.append(f"uses page-wide fallback selector {selector_node.value!r}")
        if re.search(r",\s*(?:body|html|ul|ol|li|a|ul li|ol li)\s*$", selector):
            errors.append(f"uses broad union fallback selector {selector_node.value!r}")
    return errors


def synthesize(
    manifest_path: Path,
    recon_path: Path,
    output_root: Path,
    model_path: Path,
    tool_names: list[str],
    max_new_tokens: int,
    max_images: int,
    max_image_pixels: int,
    max_input_tokens: int,
    approved_physical_gpu: int,
    max_gpu_memory_gib: int,
) -> dict[str, Any]:
    gpu_scope = require_scoped_gpu_approval(
        approved_physical_gpu,
        max_gpu_memory_gib,
    )
    import torch
    from transformers import AutoProcessor

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recon = json.loads(recon_path.read_text(encoding="utf-8"))
    tools = {tool["name"]: tool for tool in manifest["tools"]}
    unknown = sorted(set(tool_names) - set(tools))
    if unknown:
        raise ValueError(f"unknown tools: {', '.join(unknown)}")

    image_paths = _select_image_paths(recon_path, max_images)
    images = _load_bounded_images(image_paths, max_image_pixels)
    processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
    preflight_tokens: dict[str, int] = {}
    for tool_name in tool_names:
        content = [{"type": "image"} for _ in images]
        content.append({
            "type": "text",
            "text": _prompt(manifest, tools[tool_name], recon),
        })
        rendered = processor.apply_chat_template(
            [{"role": "user", "content": content}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        cpu_inputs = processor(
            text=[rendered],
            images=images or None,
            padding=True,
            return_tensors="pt",
        )
        count = int(cpu_inputs["input_ids"].numel())
        preflight_tokens[tool_name] = count
        if count > max_input_tokens:
            usage = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "backend": "local_gpu_preflight",
                "model": str(model_path),
                "input_tokens": count,
                "output_tokens": 0,
                "max_input_tokens": max_input_tokens,
                "purpose": "website_tool_code_synthesis",
                "tool": tool_name,
                "status": "rejected_input_budget",
                "usd": 0.0,
            }
            _append_jsonl(output_root / "local_model_usage.jsonl", usage)
            _append_jsonl(SPEND_LEDGER, {
                "model": str(model_path),
                "provider": "local_gpu_preflight",
                "in": count,
                "out": 0,
                "usd": 0.0,
                "purpose": usage["purpose"],
                "tool": tool_name,
                "status": usage["status"],
            })
            raise ValueError(
                f"synthesis input for {tool_name} has {count} tokens; "
                f"limit is {max_input_tokens}"
            )
    model = load_local_model(model_path, max_gpu_memory_gib)
    summary = {
        "accepted_drafts": [],
        "rejected_drafts": [],
        "preflight_input_tokens": preflight_tokens,
    }

    for tool_name in tool_names:
        content = [{"type": "image"} for _ in images]
        content.append({
            "type": "text",
            "text": _prompt(manifest, tools[tool_name], recon),
        })
        messages = [{"role": "user", "content": content}]
        rendered = processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        inputs = processor(
            text=[rendered],
            images=images or None,
            padding=True,
            return_tensors="pt",
        ).to(model.device)
        started = time.time()
        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
            )
        duration = time.time() - started
        input_length = inputs["input_ids"].shape[1]
        new_tokens = generated[:, input_length:]
        decoded = processor.batch_decode(
            new_tokens,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0]
        draft_dir = output_root / "tools" / tool_name / "drafts" / "attempt_1"
        draft_dir.mkdir(parents=True, exist_ok=True)
        (draft_dir / "raw_model_output.txt").write_text(decoded, encoding="utf-8")
        try:
            code = _extract_code(decoded)
            errors = _validate_code(code, tool_name)
        except ValueError as error:
            code, errors = "", [str(error)]
        status = "accepted_for_live_test" if not errors else "rejected_static"
        if not errors:
            (draft_dir / "draft_script.py").write_text(code, encoding="utf-8")
            summary["accepted_drafts"].append(tool_name)
        else:
            summary["rejected_drafts"].append({
                "tool": tool_name,
                "errors": errors,
            })
        usage = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "backend": "local_gpu",
            "model": str(model_path),
            "input_tokens": int(inputs["input_ids"].numel()),
            "output_tokens": int(new_tokens.numel()),
            "duration_seconds": round(duration, 3),
            "images": [str(path) for path in image_paths],
            "max_image_pixels": max_image_pixels,
            "gpu_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
            "gpu_scope": gpu_scope,
            "usd": 0.0,
            "purpose": "website_tool_code_synthesis",
            "tool": tool_name,
            "status": status,
        }
        _append_jsonl(output_root / "local_model_usage.jsonl", usage)
        _append_jsonl(SPEND_LEDGER, {
            "model": str(model_path),
            "provider": "local_gpu",
            "in": usage["input_tokens"],
            "out": usage["output_tokens"],
            "usd": 0.0,
            "purpose": usage["purpose"],
            "tool": tool_name,
        })
        (draft_dir / "static_gate.json").write_text(
            json.dumps({
                "status": status,
                "errors": errors,
                "usage": usage,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("recon", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--tool", action="append", dest="tools")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--max-new-tokens", type=int, default=3072)
    parser.add_argument("--max-images", type=int, default=4)
    parser.add_argument("--max-image-pixels", type=int, default=1_000_000)
    parser.add_argument("--max-input-tokens", type=int, default=36_000)
    parser.add_argument("--approved-physical-gpu", type=int, required=True)
    parser.add_argument("--max-gpu-memory-gib", type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.max_images <= 12:
        raise ValueError("max-images must be between 1 and 12")
    if not 250_000 <= args.max_image_pixels <= 2_500_000:
        raise ValueError("max-image-pixels must be between 250000 and 2500000")
    if not 4_096 <= args.max_input_tokens <= 65_536:
        raise ValueError("max-input-tokens must be between 4096 and 65536")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    names = args.tools or [tool["name"] for tool in manifest["tools"]]
    result = synthesize(
        args.manifest,
        args.recon,
        args.output_root,
        args.model,
        names,
        args.max_new_tokens,
        args.max_images,
        args.max_image_pixels,
        args.max_input_tokens,
        args.approved_physical_gpu,
        args.max_gpu_memory_gib,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
