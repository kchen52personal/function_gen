#!/usr/bin/env python3
"""Execute default and held cases for the weekend run's static-pass drafts.

Execution success is only a smoke result. A separate site-aware oracle must
still validate semantics before any tool is marked verified.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


SHARED_ROOT = Path("/shared/hans").resolve()
PROJECT_ROOT = Path(__file__).resolve().parents[1]


CASES: dict[str, list[tuple[str, list[str]]]] = {
    "get_service_access_button": [
        ("default", ["--service_type", "local_hall"]),
        ("held", ["--service_type", "drug_price"]),
    ],
    "get_drug_price_info": [
        ("default", ["--drug_name", "阿司匹林"]),
        ("held", ["--drug_name", "阿莫西林"]),
    ],
    "filter_services_by_category": [
        ("default", ["--category", "reimbursement"]),
        ("held", ["--category", "inquiry"]),
    ],
    "extract_button_action_type": [
        ("default", ["--button_text_pattern", "enter"]),
        ("held", ["--button_text_pattern", "地方药耗招采"]),
    ],
    "query_national_policies": [
        ("default", ["--keyword", "医保", "--limit", "5"]),
        ("held", ["--keyword", "药品", "--limit", "4"]),
    ],
    "access_official_website": [
        ("default", ["--target_section", "home"]),
        ("held", ["--target_section", "news"]),
    ],
    "search_policy_by_date": [
        ("default", ["--start_date", "2023-01-01", "--end_date", "2023-12-31"]),
        ("held", ["--start_date", "2024-01-01", "--end_date", "2024-12-31"]),
    ],
    "search_products": [
        ("default", ["--query", "milk", "--limit", "5"]),
        ("held", ["--query", "rice", "--limit", "4"]),
    ],
    "browse_categories": [
        ("default", ["--include-subcategories", "True"]),
        ("held", ["--include-subcategories", "False"]),
    ],
    "get_membership_benefits": [
        ("default", ["--member_type", "digital_club"]),
        ("held", ["--member_type", "wine_club"]),
    ],
    "search_recipes": [
        ("default", ["--ingredient", "chicken"]),
        ("held", ["--ingredient", "salmon", "--meal_type", "Quick Meals"]),
    ],
    "get_brand_catalog": [
        ("default", ["--brand_name", "FairPrice", "--sort_order", "popularity"]),
        ("held", ["--brand_name", "Nestle", "--sort_order", "name_asc"]),
    ],
}


def inside_shared(path: Path) -> Path:
    resolved = path.resolve()
    if resolved != SHARED_ROOT and SHARED_ROOT not in resolved.parents:
        raise ValueError("all paths must stay inside /shared/hans")
    return resolved


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def find_final_result(run_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    logs = sorted(run_dir.rglob("final_script_log.txt"))
    if not logs:
        return None, "missing final_script_log.txt"
    for log in logs:
        for line in reversed(log.read_text(encoding="utf-8").splitlines()):
            if line.startswith("final result: "):
                try:
                    value = json.loads(line[len("final result: "):])
                except json.JSONDecodeError as error:
                    return None, f"invalid final result JSON: {error}"
                if isinstance(value, dict):
                    return value, None
                return None, "final result is not an object"
    return None, "log lacks final result JSON"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weekend-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=150)
    args = parser.parse_args()
    weekend_root = inside_shared(args.weekend_root)
    output_root = inside_shared(args.output_root)
    if not 30 <= args.timeout_seconds <= 300:
        raise ValueError("timeout must be between 30 and 300 seconds")
    output_root.mkdir(parents=True, exist_ok=True)
    weekend = json.loads((weekend_root / "summary.json").read_text(encoding="utf-8"))
    summary: dict[str, Any] = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "weekend_root": str(weekend_root),
        "model_calls": 0,
        "gpu_jobs": 0,
        "cases": [],
    }
    summary_path = output_root / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    completed_ids = {record["case_id"] for record in summary["cases"]}
    environment = {
        **os.environ,
        "PLAYWRIGHT_BROWSERS_PATH": str(PROJECT_ROOT / ".playwright-browsers"),
        "PAGER": "cat",
        "MANPAGER": "cat",
    }

    for site in weekend["sites"]:
        for tool in site.get("tools", []):
            if tool.get("status") != "static_pass":
                continue
            name = tool["tool"]
            source = inside_shared(Path(tool["script"]))
            if name not in CASES:
                raise ValueError(f"missing smoke cases for {name}")
            for split, flags in CASES[name]:
                case_id = f"{site['site_id']}__{name}__{split}"
                if case_id in completed_ids:
                    continue
                run_dir = output_root / site["site_id"] / name / split
                run_dir.mkdir(parents=True, exist_ok=False)
                script = run_dir / "final_script.py"
                shutil.copy2(source, script)
                command = ["python", str(script), *flags]
                started = datetime.now(timezone.utc)
                try:
                    completed = subprocess.run(
                        command,
                        cwd=run_dir,
                        env=environment,
                        text=True,
                        capture_output=True,
                        timeout=args.timeout_seconds,
                        check=False,
                    )
                    exit_code = completed.returncode
                    stdout = completed.stdout
                    stderr = completed.stderr
                except subprocess.TimeoutExpired as error:
                    exit_code = 124
                    stdout = error.stdout or ""
                    stderr = (error.stderr or "") + "\ncase timed out\n"
                if not isinstance(stdout, str):
                    stdout = stdout.decode("utf-8", "replace")
                if not isinstance(stderr, str):
                    stderr = stderr.decode("utf-8", "replace")
                (run_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
                (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
                result, result_error = find_final_result(run_dir)
                screenshots = [
                    path for path in run_dir.rglob("*.png") if path.stat().st_size > 0
                ]
                errors: list[str] = []
                if exit_code != 0:
                    errors.append(f"process exit {exit_code}")
                if result_error:
                    errors.append(result_error)
                if len(screenshots) < 2:
                    errors.append(f"only {len(screenshots)} non-empty screenshots")
                record = {
                    "case_id": case_id,
                    "site_id": site["site_id"],
                    "tool": name,
                    "split": split,
                    "flags": flags,
                    "status": "execution_smoke_pass" if not errors else "execution_smoke_fail",
                    "errors": errors,
                    "exit_code": exit_code,
                    "duration_seconds": round(
                        (datetime.now(timezone.utc) - started).total_seconds(), 3
                    ),
                    "run_dir": str(run_dir),
                    "result": result,
                    "screenshot_count": len(screenshots),
                    "independent_semantic_verification": "pending" if not errors else "not_run",
                    "counts_as_verified": False,
                }
                summary["cases"].append(record)
                completed_ids.add(case_id)
                write_json(summary_path, summary)

    passed = sum(case["status"] == "execution_smoke_pass" for case in summary["cases"])
    summary.update({
        "status": "complete",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "execution_smoke_pass": passed,
        "execution_smoke_fail": len(summary["cases"]) - passed,
        "total": len(summary["cases"]),
        "verified_tools": 0,
    })
    write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
