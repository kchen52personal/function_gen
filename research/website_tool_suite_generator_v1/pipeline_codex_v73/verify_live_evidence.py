#!/usr/bin/env python3
"""Audit evidence files only; this does NOT verify tool correctness.

Kept for historical bundles. An exit code, log and screenshots are not an
independent oracle. This script must never certify a suite as verified.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def verify_run(site_root: Path, run: dict, tool_name: str) -> list[str]:
    errors: list[str] = []
    run_dir = site_root / run.get("path", "")
    script = run_dir / "final_script.py"
    log = run_dir / "final_script_log.txt"
    screenshots = sorted((run_dir / "screenshots").glob("*.png"))
    if run.get("exit_code") != 0:
        errors.append(f"{tool_name}: {run_dir} has non-zero or missing exit_code")
    if not script.is_file():
        errors.append(f"{tool_name}: missing {script}")
    if not log.is_file():
        errors.append(f"{tool_name}: missing {log}")
    else:
        text = log.read_text(encoding="utf-8")
        if "step 0 action: params:" not in text:
            errors.append(f"{tool_name}: {log} lacks parameter evidence")
        if "final result: " not in text:
            errors.append(f"{tool_name}: {log} lacks a final structured result")
    if len(screenshots) < 2:
        errors.append(f"{tool_name}: {run_dir} has fewer than two screenshots")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("site_root", type=Path)
    args = parser.parse_args()

    manifest = json.loads((args.site_root / "site_manifest.json").read_text(encoding="utf-8"))
    ledger = json.loads((args.site_root / "live_verification.json").read_text(encoding="utf-8"))
    manifest_names = {tool["name"] for tool in manifest["tools"]}
    ledger_names = set(ledger["tools"])
    errors: list[str] = []
    if len(manifest_names) != 10:
        errors.append(f"manifest contains {len(manifest_names)} unique tools, expected 10")
    if manifest_names != ledger_names:
        errors.append("manifest and verification ledger tool names differ")

    verified = 0
    for tool_name, record in ledger["tools"].items():
        counts = record.get("counts_toward_final_ten", False)
        if counts and record.get("status") != "verified":
            errors.append(f"{tool_name}: counts despite status={record.get('status')!r}")
        if not counts:
            continue
        verified += 1
        default = record.get("default_run", {})
        held = record.get("held_run", {})
        if not default or not held:
            errors.append(f"{tool_name}: missing default or held run")
            continue
        if default.get("arguments") == held.get("arguments"):
            errors.append(f"{tool_name}: held arguments are identical to default arguments")
        errors.extend(verify_run(args.site_root, default, tool_name))
        errors.extend(verify_run(args.site_root, held, tool_name))

    if verified != ledger.get("verified_tools"):
        errors.append(
            f"ledger says {ledger.get('verified_tools')} verified but {verified} count"
        )
    result = {
        "status": "evidence_only" if not errors else "fail",
        "suite_status": "semantic_verification_not_established",
        "claimed_verified_tools": verified,
        "verified_tools": 0,
        "required_tools": 10,
        "errors": errors,
        "limitation": "Evidence-file presence and ledger claims do not establish tool correctness.",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # This legacy checker has no independent semantic oracle; never present a
    # successful process exit as a verified tool suite.
    raise SystemExit(1)


if __name__ == "__main__":
    main()
