#!/usr/bin/env python3
"""Adapt verified site-tool runs to WebWright Skill Factory's input layout.

This stage is deterministic and makes no model, API, browser, or GPU calls.
It does not run Skill Factory distillation; that later stage requires a model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Any


FINAL_PREFIX = "final result: "


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_result(log_path: Path) -> dict[str, Any]:
    for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
        if line.startswith(FINAL_PREFIX):
            value = json.loads(line[len(FINAL_PREFIX):])
            if not isinstance(value, dict):
                raise ValueError(f"{log_path}: final result is not an object")
            return value
    raise ValueError(f"{log_path}: missing final structured result")


def _task_text(
    site_name: str,
    tool: dict[str, Any],
    arguments: dict[str, Any],
) -> str:
    return (
        f"On {site_name}, {tool['goal'].rstrip('.')}. "
        f"Use these arguments: {json.dumps(arguments, ensure_ascii=False, sort_keys=True)}. "
        f"Return {tool['returns']}. "
        f"Success requires: {tool['success_contract']}"
    )


def build(site_root: Path, output_root: Path) -> dict[str, Any]:
    manifest = _read_json(site_root / "site_manifest.json")
    ledger = _read_json(site_root / "live_verification.json")
    tools = {tool["name"]: tool for tool in manifest["tools"]}
    if len(tools) != 10 or ledger.get("verified_tools") != 10:
        raise ValueError("adapter requires exactly ten verified tools")

    output_root.mkdir(parents=True, exist_ok=True)
    golds: dict[str, Any] = {}
    records: list[dict[str, Any]] = []

    for tool_name, tool in tools.items():
        verification = ledger["tools"].get(tool_name, {})
        if verification.get("status") != "verified":
            raise ValueError(f"{tool_name}: not verified")
        for split, run_key in (("default", "default_run"), ("held", "held_run")):
            run = verification[run_key]
            if run.get("exit_code") != 0:
                raise ValueError(f"{tool_name}/{split}: nonzero exit code")
            source_run = site_root / run["path"]
            source_script = source_run / "final_script.py"
            source_log = source_run / "final_script_log.txt"
            screenshots = sorted((source_run / "screenshots").glob("*.png"))
            if not source_script.is_file() or not source_log.is_file():
                raise ValueError(f"{tool_name}/{split}: missing script or log")
            if len(screenshots) < 2:
                raise ValueError(f"{tool_name}/{split}: fewer than two screenshots")
            result = _read_result(source_log)
            if result.get("tool") != tool_name:
                raise ValueError(f"{tool_name}/{split}: result tool mismatch")

            task_id = f"netease_{tool_name}_{split}"
            run_dir = output_root / task_id
            run_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_script, run_dir / "final_script.py")
            task = {
                "task_id": task_id,
                "task": _task_text(
                    manifest["site_name"],
                    tool,
                    run["arguments"],
                ),
                "start_url": manifest["start_url"],
            }
            response = {
                "retrieved_data": result,
                "status": "SUCCESS",
            }
            provenance = {
                "source_run": str(source_run.relative_to(site_root)),
                "source_log": str(source_log.relative_to(site_root)),
                "source_screenshots": [
                    str(path.relative_to(site_root)) for path in screenshots
                ],
                "arguments": run["arguments"],
                "verifier_outcome": "pass",
                "note": (
                    "Reference answer comes from a live run that passed the suite evidence "
                    "gate and screenshot review; it is not an external benchmark label."
                ),
            }
            (run_dir / "task.json").write_text(
                json.dumps(task, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (run_dir / "agent_response.json").write_text(
                json.dumps(response, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (run_dir / "provenance.json").write_text(
                json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            golds[task_id] = result
            records.append({
                "task_id": task_id,
                "tool": tool_name,
                "split": split,
                "arguments": run["arguments"],
                "run_dir": str(run_dir),
            })

    if len(records) != 20:
        raise ValueError(f"expected 20 adapted runs, found {len(records)}")
    (output_root / "golds.json").write_text(
        json.dumps(golds, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        "status": "ready_for_distillation",
        "site": manifest["site_name"],
        "tools": len(tools),
        "runs": len(records),
        "runs_per_tool": 2,
        "model_calls": 0,
        "api_cost_usd": 0.0,
        "records": records,
        "next_command_template": (
            "python -m cuawright.webwright.skill_factory learn <this-directory> "
            "--library <library-directory> --golds <this-directory>/golds.json"
        ),
        "caveat": (
            "The adapter is complete, but Skill Factory grouping/distillation has not run. "
            "Reference answers are verified live outputs, not external benchmark labels."
        ),
    }
    (output_root / "adapter_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("site_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = build(args.site_root, args.output)
    print(json.dumps({
        key: summary[key]
        for key in ("status", "site", "tools", "runs", "model_calls", "api_cost_usd")
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
