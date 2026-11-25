#!/usr/bin/env python3
"""Use a local vision-language model to propose site-function candidates."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import time
from typing import Any

from PIL import Image


DEFAULT_MODEL = Path("/shared/hans/models/OpenWebRL-4B-SFT")
SPEND_LEDGER = Path("/shared/hans/webarena/spend_ledger.jsonl")


def require_scoped_gpu_approval(
    approved_physical_gpu: int,
    max_gpu_memory_gib: int,
) -> dict[str, Any]:
    """Fail before model loading unless the launch matches explicit approval.

    CUDA_VISIBLE_DEVICES remaps the approved physical GPU to logical cuda:0.
    The memory fraction is a hard PyTorch allocator ceiling for this process;
    ``max_memory`` below also constrains Transformers/Accelerate placement.
    """
    if os.environ.get("WEBSITE_TOOL_GPU_APPROVED") != "YES":
        raise RuntimeError(
            "GPU launch refused: WEBSITE_TOOL_GPU_APPROVED must equal YES"
        )
    approved_env = os.environ.get("WEBSITE_TOOL_APPROVED_PHYSICAL_GPU", "")
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "")
    expected = str(approved_physical_gpu)
    if approved_env != expected or visible != expected:
        raise RuntimeError(
            "GPU launch refused: --approved-physical-gpu, "
            "WEBSITE_TOOL_APPROVED_PHYSICAL_GPU, and CUDA_VISIBLE_DEVICES "
            "must name the same single physical GPU"
        )
    if not 4 <= max_gpu_memory_gib <= 80:
        raise ValueError("max-gpu-memory-gib must be between 4 and 80")
    import torch

    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("GPU launch refused: expected exactly one visible CUDA device")
    total_bytes = torch.cuda.get_device_properties(0).total_memory
    cap_bytes = max_gpu_memory_gib * 1024**3
    if cap_bytes > total_bytes:
        raise RuntimeError(
            f"GPU memory cap {max_gpu_memory_gib} GiB exceeds visible device capacity"
        )
    torch.cuda.set_per_process_memory_fraction(cap_bytes / total_bytes, device=0)
    return {
        "approved_physical_gpu": approved_physical_gpu,
        "logical_gpu": 0,
        "max_gpu_memory_gib": max_gpu_memory_gib,
        "visible_device_name": torch.cuda.get_device_name(0),
    }


def load_local_model(model_path: Path, max_gpu_memory_gib: int):
    """Load the local checkpoint within the approved visible-device cap."""
    import torch
    from transformers import Qwen3VLForConditionalGeneration

    return Qwen3VLForConditionalGeneration.from_pretrained(
        model_path,
        local_files_only=True,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        max_memory={0: f"{max_gpu_memory_gib}GiB", "cpu": "64GiB"},
        attn_implementation="sdpa",
    ).eval()


def _compact_recon(recon: dict[str, Any]) -> dict[str, Any]:
    def evidence_id(page: int, surface: int, kind: str, index: int) -> str:
        return f"p{page:02d}.s{surface:02d}.{kind}.{index:03d}"

    def compact_record(
        item: dict[str, Any], page: int, surface: int, kind: str, index: int
    ) -> dict[str, Any]:
        record = {
            key: item[key]
            for key in ("text", "href", "title", "aria_label", "name", "type", "role")
            if item.get(key) not in (None, "", False)
        }
        return {
            "evidence_id": evidence_id(page, surface, kind, index),
            **record,
        }

    pages = []
    for page_index, page in enumerate(recon["pages"]):
        surfaces = []
        for surface_index, frame in enumerate(page.get("frames", [])):
            primary_links = [
                (index, item)
                for index, item in enumerate(frame.get("links", []))
                if item.get("in_primary_chrome")
            ][:12]
            other_links = [
                (index, item)
                for index, item in enumerate(frame.get("links", []))
                if not item.get("in_primary_chrome")
            ][:12]
            content = [
                (index, item)
                for index, item in enumerate(frame.get("content", []))
                if item.get("text", "")
            ][:24]
            surfaces.append({
                "url": frame.get("url"),
                "headings": [
                    compact_record(
                        item, page_index, surface_index, "heading", index
                    )
                    for index, item in enumerate(frame.get("headings", [])[:8])
                ],
                "primary_links": [
                    compact_record(item, page_index, surface_index, "link", index)
                    for index, item in primary_links
                ],
                "other_links": [
                    compact_record(item, page_index, surface_index, "link", index)
                    for index, item in other_links
                ],
                "actions": [
                    compact_record(item, page_index, surface_index, "action", index)
                    for index, item in enumerate(frame.get("actions", [])[:8])
                ],
                "fields": [
                    compact_record(item, page_index, surface_index, "field", index)
                    for index, item in enumerate(frame.get("fields", [])[:8])
                ],
                "forms": [
                    compact_record(item, page_index, surface_index, "form", index)
                    for index, item in enumerate(frame.get("forms", [])[:3])
                ],
                "content_samples": [
                    compact_record(item, page_index, surface_index, "content", index)
                    for index, item in content
                ],
            })
        pages.append({
            "url": page.get("final_url"),
            "title": page.get("title"),
            "http_status": page.get("http_status"),
            "surfaces": surfaces,
        })
    return {
        "start_url": recon["start_url"],
        "allowed_host": recon["allowed_host"],
        "pages": pages,
        "repeated_routes": recon.get("repeated_first_party_routes", [])[:20],
    }


def _prompt(recon: dict[str, Any]) -> str:
    schema = {
        "site_id": "snake_case_identifier",
        "site_name": "human-readable site name",
        "start_url": "https://...",
        "allowed_hosts": ["host"],
        "candidates": [{
            "name": "snake_case_tool_name",
            "effect_key": "unique.behavior",
            "goal_class": "unique_goal_class",
            "output_object_type": "unique_terminal_object",
            "choose_when": "when a user should choose this tool",
            "exclude_when": "nearby requests this tool must not handle",
            "goal": "one complete user-level functionality",
            "arguments": [{
                "name": "argument_name",
                "type": "str|int|bool",
                "default": "a value supported by observed evidence",
                "constraints": "validation rule",
            }],
            "test_cases": {
                "default": {
                    "argument_name": "the same typed value as arguments[].default",
                },
                "held": {
                    "argument_name": "a different plausible typed value hidden from the code writer",
                },
            },
            "returns": {
                "field_name": "str|int|float|bool|object|list|list[object]|list[str]",
                "second_field": "str|int|float|bool|object|list|list[object]|list[str]",
            },
            "return_evidence": {
                "field_name": {
                    "evidence_id": "p00.s00.content.000",
                },
                "second_field": {
                    "evidence_id": "p00.s00.link.001",
                },
            },
            "boundary": "what this tool explicitly does not do",
            "success_contract": "observable extraction postcondition beyond reaching a URL",
            "popularity_proxy": {
                "claim": "high|medium",
                "signals": ["p00.s00.link.001"],
            },
            "evidence": {
                "page_url": "observed URL",
                "evidence_id": "p00.s00.heading.000",
            },
            "priority": 1,
        }],
    }
    return f"""You are proposing candidates for a website-to-code-tool pipeline.

Return JSON only, following this schema:
{json.dumps(schema, ensure_ascii=False, indent=2)}

Propose up to 6 READ-ONLY candidates. Return fewer, including an empty array,
if the evidence cannot support more. A single complete and testable candidate
is more useful than 16 speculative ones. A tool is one parameterized program
that completes one recognizable user goal and returns a distinct terminal
object. It is not a primitive click/type/scroll.

Prioritize complete user goals such as finding relevant records or retrieving
the information a visitor came for, not merely listing menus, field options,
or navigation controls. Keep returned fields to the smallest useful set
supported by the observed page; do not add extra fields just to make the tool
look comprehensive. Never claim personalized eligibility without collecting
every input necessary to determine it. An argument that selects among pages
but returns the same fixed values for every input is not a meaningful
parameterized function: discard that proposal.

Cardinality and parameter contract:
- EVERY candidate MUST have at least one genuine user-supplied argument;
- a fixed homepage section is not parameterized: generalize it honestly with
  an observed selector such as query, article_title, category, date, namespace,
  language, sort, or limit when the site evidence supports that behavior;
- never add a meaningless argument merely to satisfy the schema;
- if a proposed function cannot support a real argument, discard it and find a
  different complete functionality before returning JSON.

Hard distinctness rules:
- different category, language, region, sort, query, or fixed value is an
  argument change, NEVER a new tool;
- reject synonyms that return the same object;
- reject consecutive primitive steps of one natural function;
- navigation-only tools and URL-only outputs are invalid; every tool must
  extract and return useful structured site data after reaching the page;
- every effect_key, goal_class, and output_object_type must be unique;
- choose_when and exclude_when must make nearby functions contrastive.

Contrastive examples:
- INVALID: open_product_page(product_id) -> product URL. This only navigates.
- VALID: get_product_details(product_id) -> name, price, availability, URL.
- INVALID: browse_rock_music and browse_jazz_music as separate tools.
- VALID: browse_music(category, sort, limit) -> structured music records.
- INVALID: open_chart(chart_name) -> chart URL.
- VALID: get_chart(chart_name, limit) -> ranked title/artist records and URLs.

Before returning JSON, reject any candidate when its goal can be summarized as
"open/navigate/view a page", its return is only a URL, or its success condition
only checks that the browser reached a page. Each accepted candidate must name
the useful fields it extracts from that page. Every captured DOM item has an
evidence_id. Every key in returns must have a same-named return_evidence entry
that cites exactly one of those IDs. Never copy or rewrite the evidence text.
Evidence for one field does not support a different field: for example, a 评论
heading cannot support lyrics, and 热门新碟 cannot support trending tracks. If a
promised field lacks a matching captured evidence ID, remove that field or
reject the candidate.

Schema completeness is mandatory:
- every argument object has name, type, default, and a non-empty constraints;
- for bounded integer arguments, express limits exactly as "between X and Y
  inclusive" with 0 <= X <= Y <= 100; unsupported or ambiguous bounded ranges
  cannot receive automatic boundary checks and must not be proposed;
- test_cases has exactly default and held; both contain every declared argument
  and no others; the default values exactly match arguments[].default; at least
  one held value differs; use two plausible public inputs for the observed site
  rather than placeholders such as test, example, foo, or 123;
- popularity_proxy has claim and at least one non-empty signals entry;
- every return field has exact return_evidence as defined above.
- every returns value is exactly one of str, int, float, bool, object, list,
  list[object], or list[str]; do not write prose descriptions as types.
- output_object_type is a descriptive unique name such as paper_search_results,
  NOT a schema type such as list[object]. The outer result is always an object:
  when returning many items, declare a list-valued FIELD in returns, for
  example {{"items": "list[object]"}}, not separate scalar title and URL
  fields. Describe each item's fields in success_contract and cite observed
  evidence for the items field. Never promise optional fields the schema
  requires to be nonempty on every case.
- JSON types must agree: for an int argument use an unquoted number such as
  10 in both arguments[].default and test_cases.default, not "10"; use true
  or false for bool. Copy every argument's default exactly into the default
  test case, then change at least one value in the held case.

Before returning JSON, verify that every candidate has a non-empty
arguments array. Remove any candidate with no real argument rather than using
an empty array. Use at most 6 candidates and do not fill space with guesses.

To improve reproducibility, define outputs with precise, independently
observable meanings: say exactly which text or attribute to extract, its
ordering, and any truncation rule. Prefer simple, stable fields supported by
the observed pages. Do not promise a list in the goal but declare scalar fields
in the return schema. Avoid vague fields such as "summary" unless you specify
exactly which visible passage is meant. Use two different inputs that a public
visitor can actually supply, and ensure changing the input changes the answer.
Every field promised by the goal must be present in returns; do not promise a
description, portal URL, or other output that the declared result cannot carry.
If the goal promises links, return their URLs, not only their visible labels;
if only labels are intended, say link text or link labels in the goal.
Avoid unspecified "sample content": for any sample state exactly which first
or last paragraph, sentence, or number of items/characters to return.
For a headline_text field, specify whether it is the full news item/sentence
or the first article link's text; never leave those meanings interchangeable.
The reconnaissance keys primary_links and other_links are collection buckets,
not page section names. Do not describe either bucket as a website section;
identify an actual visible heading and exact item boundaries instead.
Evidence IDs such as p00.s00.heading.001 are internal snapshot labels used to
cite observations; they are not website data and must never be returned as
tool outputs. A real HTML element ID is different and needs real page evidence.
Do not label a section heading as the type of a metadata field; use accurate,
unambiguous names for each promised value.
For a finite set of observed options, list the complete allowed set; examples
such as "one of the observed headings, such as ..." do not define an
exhaustive testable domain. Do not present examples as a complete set.
For each list, say exactly what counts as one item, whether to include nested
items, whether the text is a whole item or only a link or heading, its source
order, and how an empty or short list is handled. For each scalar, say exactly
which visible element or attribute supplies it. If the evidence does not let
you define those details without guessing, simplify the output or omit the
candidate; do not use vague alternatives such as "titles or descriptions" or
unspecified "sub-sections". Preserve the site's real meaning rather than
inventing a convenient interpretation.

Popularity is only a proxy. Support it with visible primary navigation,
homepage prominence, repetition, and core-workflow relevance. Do not invent
telemetry. Do not claim a function works merely because text exists: cite the
observed page/control as candidate evidence. Login, purchasing, posting,
deletion, account settings, and irreversible actions are out of scope.
Nondeterministic functions such as random/shuffle/pick-for-me are also out of
scope because an independent implementation cannot exactly replicate them.
Every argument must affect the live website operation or output; never invent
a seed, limit, filter, or other argument that the site/tool will ignore.

Reconnaissance:
{json.dumps(_compact_recon(recon), ensure_ascii=False, indent=2)}
"""


def _select_image_paths(recon_path: Path, max_images: int) -> list[Path]:
    """Evenly sample the diversified crawl instead of taking only early pages."""
    paths = sorted((recon_path.parent / "screenshots").glob("*.png"))
    if len(paths) <= max_images:
        return paths
    if max_images == 1:
        return [paths[0]]
    indices = [
        round(index * (len(paths) - 1) / (max_images - 1))
        for index in range(max_images)
    ]
    return [paths[index] for index in indices]


def _load_bounded_images(
    image_paths: list[Path],
    max_image_pixels: int,
) -> list[Image.Image]:
    """Load RGB screenshots and downscale each to a bounded visual-token budget."""
    images: list[Image.Image] = []
    for path in image_paths:
        image = Image.open(path).convert("RGB")
        pixels = image.width * image.height
        if pixels > max_image_pixels:
            scale = math.sqrt(max_image_pixels / pixels)
            size = (
                max(1, math.floor(image.width * scale)),
                max(1, math.floor(image.height * scale)),
            )
            image = image.resize(size, Image.Resampling.LANCZOS)
        images.append(image)
    return images


def _extract_json(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "candidates" in value:
            return value
    raise ValueError("model output contained no valid candidate JSON object")


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def propose(
    recon_path: Path,
    output_path: Path,
    model_path: Path,
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

    recon = json.loads(recon_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image_paths = _select_image_paths(recon_path, max_images)
    images = _load_bounded_images(image_paths, max_image_pixels)
    content = [{"type": "image"} for _ in images]
    content.append({"type": "text", "text": _prompt(recon)})
    messages = [{"role": "user", "content": content}]

    processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
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
    )
    input_tokens = int(inputs["input_ids"].numel())
    if input_tokens > max_input_tokens:
        usage = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "backend": "local_gpu_preflight",
            "model": str(model_path),
            "input_tokens": input_tokens,
            "output_tokens": 0,
            "images": [str(path) for path in image_paths],
            "max_image_pixels": max_image_pixels,
            "max_input_tokens": max_input_tokens,
            "gpu_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
            "gpu_scope": gpu_scope,
            "usd": 0.0,
            "purpose": "website_function_candidate_proposal",
            "output": str(output_path),
            "status": "rejected_input_budget",
            "error": f"input token count {input_tokens} exceeds cap {max_input_tokens}",
        }
        _append_jsonl(output_path.parent / "local_model_usage.jsonl", usage)
        (output_path.parent / "runtime_failure.json").write_text(
            json.dumps(usage, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(usage["error"])
    model = load_local_model(model_path, max_gpu_memory_gib)
    inputs = inputs.to(model.device)
    started = time.time()
    try:
        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
            )
    except Exception as error:
        duration = time.time() - started
        usage = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "backend": "local_gpu",
            "model": str(model_path),
            "input_tokens": int(inputs["input_ids"].numel()),
            "output_tokens": 0,
            "duration_seconds": round(duration, 3),
            "images": [str(path) for path in image_paths],
            "max_image_pixels": max_image_pixels,
            "max_input_tokens": max_input_tokens,
            "gpu_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
            "gpu_scope": gpu_scope,
            "usd": 0.0,
            "purpose": "website_function_candidate_proposal",
            "output": str(output_path),
            "status": "rejected_runtime",
            "error_type": type(error).__name__,
            "error": str(error)[:2000],
        }
        _append_jsonl(output_path.parent / "local_model_usage.jsonl", usage)
        _append_jsonl(SPEND_LEDGER, {
            "model": str(model_path),
            "provider": "local_gpu",
            "in": usage["input_tokens"],
            "out": 0,
            "usd": 0.0,
            "purpose": usage["purpose"],
            "status": usage["status"],
            "error_type": usage["error_type"],
        })
        (output_path.parent / "runtime_failure.json").write_text(
            json.dumps(usage, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise
    duration = time.time() - started
    input_length = inputs["input_ids"].shape[1]
    new_tokens = generated[:, input_length:]
    decoded = processor.batch_decode(
        new_tokens, skip_special_tokens=True, clean_up_tokenization_spaces=False
    )[0]
    raw_output_path = output_path.parent / "raw_model_output.txt"
    raw_output_path.write_text(decoded, encoding="utf-8")
    generation = {
        "backend": "local_gpu",
        "model": str(model_path),
        "input_tokens": int(inputs["input_ids"].numel()),
        "output_tokens": int(new_tokens.numel()),
        "duration_seconds": round(duration, 3),
        "images": [str(path) for path in image_paths],
        "max_image_pixels": max_image_pixels,
        "max_input_tokens": max_input_tokens,
        "gpu_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
        "gpu_scope": gpu_scope,
        "usd": 0.0,
        "raw_output": str(raw_output_path),
    }
    usage_path = output_path.parent / "local_model_usage.jsonl"
    try:
        result = _extract_json(decoded)
    except ValueError as error:
        usage = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **generation,
            "purpose": "website_function_candidate_proposal",
            "output": str(output_path),
            "status": "rejected_parse",
            "error": str(error),
        }
        _append_jsonl(usage_path, usage)
        _append_jsonl(SPEND_LEDGER, {
            "model": str(model_path),
            "provider": "local_gpu",
            "in": usage["input_tokens"],
            "out": usage["output_tokens"],
            "usd": 0.0,
            "purpose": usage["purpose"],
            "status": usage["status"],
        })
        raise
    generation["status"] = "accepted_parse"
    result["generation"] = generation
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    usage = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **result["generation"],
        "purpose": "website_function_candidate_proposal",
        "output": str(output_path),
    }
    _append_jsonl(usage_path, usage)
    _append_jsonl(SPEND_LEDGER, {
        "model": str(model_path),
        "provider": "local_gpu",
        "in": usage["input_tokens"],
        "out": usage["output_tokens"],
        "usd": 0.0,
        "purpose": usage["purpose"],
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("recon", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--max-new-tokens", type=int, default=4096)
    parser.add_argument("--max-images", type=int, default=4)
    parser.add_argument("--max-image-pixels", type=int, default=500_000)
    parser.add_argument("--max-input-tokens", type=int, default=36_000)
    parser.add_argument("--approved-physical-gpu", type=int, required=True)
    parser.add_argument("--max-gpu-memory-gib", type=int, required=True)
    args = parser.parse_args()
    if not 256 <= args.max_new_tokens <= 8192:
        raise ValueError("max-new-tokens must be between 256 and 8192")
    if not 1 <= args.max_images <= 12:
        raise ValueError("max-images must be between 1 and 12")
    if not 250_000 <= args.max_image_pixels <= 2_500_000:
        raise ValueError("max-image-pixels must be between 250000 and 2500000")
    if not 8_000 <= args.max_input_tokens <= 50_000:
        raise ValueError("max-input-tokens must be between 8000 and 50000")
    result = propose(
        args.recon,
        args.output,
        args.model,
        args.max_new_tokens,
        args.max_images,
        args.max_image_pixels,
        args.max_input_tokens,
        args.approved_physical_gpu,
        args.max_gpu_memory_gib,
    )
    print(json.dumps({
        "candidates": len(result["candidates"]),
        "output": str(args.output),
        "usage": result["generation"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
