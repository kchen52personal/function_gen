#!/usr/bin/env python3
"""Independent semantic oracle for Amazon.sg search_help_topics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from local_synthesize import _validate_code


TERMS = {
    "shipping": {"shipping", "delivery"},
    "returns": {"return", "returns", "refund"},
}


def _read_case(run_dir: Path, query: str) -> tuple[dict, list[str]]:
    errors: list[str] = []
    log = run_dir / "final_script_log.txt"
    screenshots = run_dir / "screenshots"
    if not log.is_file():
        return {}, [f"{query}: missing final_script_log.txt"]
    lines = log.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].startswith("step 0 action: params:"):
        errors.append(f"{query}: missing parameter evidence on first log line")
    if not lines or not lines[-1].startswith("final result: "):
        errors.append(f"{query}: missing final result on last log line")
        return {}, errors
    try:
        result = json.loads(lines[-1].removeprefix("final result: "))
    except json.JSONDecodeError as error:
        errors.append(f"{query}: invalid final JSON: {error}")
        return {}, errors
    if result.get("query") != query:
        errors.append(f"{query}: output query does not match input")
    topics = result.get("help_topics")
    if not isinstance(topics, list) or len(topics) < 5:
        errors.append(f"{query}: fewer than five returned topics")
        topics = topics if isinstance(topics, list) else []
    titles: list[str] = []
    relevant = 0
    for index, topic in enumerate(topics):
        if not isinstance(topic, dict):
            errors.append(f"{query}: topic {index} is not an object")
            continue
        title = str(topic.get("title", "")).strip()
        summary = str(topic.get("summary", "")).strip()
        url = str(topic.get("url", "")).strip()
        if not title or title.casefold() == "main content":
            errors.append(f"{query}: topic {index} has invalid title")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "www.amazon.sg":
            errors.append(f"{query}: topic {index} is not a first-party HTTPS URL")
        haystack = f"{title} {summary}".casefold()
        if any(term in haystack for term in TERMS[query]):
            relevant += 1
        titles.append(title)
    if len(titles) != len(set(titles)):
        errors.append(f"{query}: duplicate titles")
    if relevant < max(3, len(topics) // 2):
        errors.append(f"{query}: fewer than half the topics are visibly relevant")
    for name in ("before.png", "after.png"):
        image = screenshots / name
        if not image.is_file() or image.stat().st_size < 10_000:
            errors.append(f"{query}: missing or implausibly small {name}")
    return result, errors


def verify(tool_root: Path) -> dict:
    script = tool_root / "final_script.py"
    errors: list[str] = []
    if not script.is_file():
        errors.append("missing final_script.py")
    else:
        errors.extend(_validate_code(script.read_text(encoding="utf-8"), "search_help_topics"))
    default, default_errors = _read_case(tool_root / "final_runs" / "default", "shipping")
    held, held_errors = _read_case(tool_root / "final_runs" / "held", "returns")
    errors.extend(default_errors)
    errors.extend(held_errors)
    default_titles = {x.get("title") for x in default.get("help_topics", [])}
    held_titles = {x.get("title") for x in held.get("help_topics", [])}
    if default_titles and held_titles:
        overlap = len(default_titles & held_titles) / len(default_titles | held_titles)
        if overlap >= 0.5:
            errors.append("default and held outputs changed too little")
    return {
        "status": "verified" if not errors else "rejected",
        "tool": "search_help_topics",
        "default_query": "shipping",
        "default_count": len(default.get("help_topics", [])),
        "held_query": "returns",
        "held_count": len(held.get("help_topics", [])),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tool_root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.tool_root)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    if result["status"] != "verified":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
