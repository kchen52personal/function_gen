#!/usr/bin/env python3
"""Resumable cross-site proposal plus actual-WebWright code-generation queue.

This deliberately reports generated/static-pass drafts separately from live,
independently verified tools. It never promotes a draft to verified.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import yaml

from local_synthesize import _prompt, _validate_code


SHARED_ROOT = Path("/shared/hans").resolve()
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_ROOT = PROJECT_ROOT / "pipeline"
WEBWRIGHT_SRC = PROJECT_ROOT / "vendor" / "Webwright" / "src"


def inside_shared(path: Path) -> Path:
    resolved = path.resolve()
    if resolved != SHARED_ROOT and SHARED_ROOT not in resolved.parents:
        raise ValueError("all paths must remain inside /shared/hans")
    return resolved


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_logged(
    command: list[str],
    *,
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: int,
) -> int:
    environment = {
        **os.environ,
        "PLAYWRIGHT_BROWSERS_PATH": str(PROJECT_ROOT / ".playwright-browsers"),
        "PYTHONPATH": f"{WEBWRIGHT_SRC}:{PIPELINE_ROOT}",
        "XDG_CONFIG_HOME": str(PROJECT_ROOT / ".webwright-config"),
        "PAGER": "cat",
        "MANPAGER": "cat",
    }
    started = datetime.now(timezone.utc)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = (error.stderr or "") + f"\nTimed out after {timeout_seconds}s\n"
        exit_code = 124
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(
        stdout if isinstance(stdout, str) else stdout.decode("utf-8", "replace"),
        encoding="utf-8",
    )
    stderr_path.write_text(
        stderr if isinstance(stderr, str) else stderr.decode("utf-8", "replace"),
        encoding="utf-8",
    )
    (stdout_path.parent / "command.json").write_text(
        json.dumps({
            "command": command,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": exit_code,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return exit_code


def webwright_config(
    *,
    system_template: str,
    api_log_path: Path,
    key_file: Path,
) -> dict[str, Any]:
    return {
        "model": {
            "model_class": "webwright_yibu_model.YibuWebWrightModel",
            "model_name": "qwen3.5-plus",
            "openrouter_endpoint": "https://yibuapi.com/v1/chat/completions",
            "provider_require_parameters": False,
            "api_key_file": str(key_file),
            "api_log_path": str(api_log_path),
            "spend_ledger_path": "/shared/hans/webarena/spend_ledger.jsonl",
            "max_api_calls": 1,
            "max_output_tokens": 8192,
            "request_timeout_seconds": 240,
            "attach_observation_screenshot": False,
        },
        "environment": {
            "environment_class": "webwright_safe_environment.SafeToolWorkspaceEnvironment",
            "command_timeout_seconds": 120,
            "shell": "/bin/bash",
            "browser_mode": "local",
            "output_truncation_chars": 20000,
            "final_script_preview_chars": 8000,
            "recent_files_limit": 50,
            "env": {
                "PLAYWRIGHT_BROWSERS_PATH": str(PROJECT_ROOT / ".playwright-browsers"),
                "PAGER": "cat",
                "MANPAGER": "cat",
            },
        },
        "agent": {
            "agent_class": "default",
            "debug_log": True,
            "step_limit": 1,
            "require_self_reflection_success": False,
            "summary_every_n_steps": 0,
            "system_template": system_template,
            "instance_template": (
                "Task: {{ task }}\nTask ID: {{ task_id }}\n"
                "Start URL: {{ start_url }}\nWorkspace: {{ workspace_dir }}\n\n"
                "Write final_script.py now."
            ),
        },
    }


def code_system_prompt(manifest: dict[str, Any], tool: dict[str, Any], recon: dict[str, Any]) -> str:
    requirements = _prompt(manifest, tool, recon)
    requirements = requirements.replace(
        "Write one standalone Python Playwright Firefox CLI tool.\n"
        "Return only one ```python code fence.\n",
        "",
        1,
    )
    return (
        "You are the code-writing agent inside actual WebWright/CUAWright. "
        "Return exactly one JSON object with thought, bash_command, done, and "
        "final_response. Your single bash command must write final_script.py in "
        "the current workspace. Use a safe base64 or quoted printf technique; "
        "do not read local files, environment variables, or secrets. Set done "
        "to true after writing the file. Do not browse or install anything in "
        "this generation turn; the preserved reconnaissance below is the only "
        "site evidence.\n\n"
        + requirements
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--max-sites", type=int, default=10)
    parser.add_argument("--max-tools-per-site", type=int, default=10)
    args = parser.parse_args()

    queue_path = inside_shared(args.queue)
    output_root = inside_shared(args.output_root)
    key_file = inside_shared(args.api_key_file)
    if not key_file.is_file() or key_file.stat().st_size == 0:
        raise ValueError("API key file is missing or empty")
    if not 1 <= args.max_sites <= 10:
        raise ValueError("max-sites must be between 1 and 10")
    if not 1 <= args.max_tools_per_site <= 10:
        raise ValueError("max-tools-per-site must be between 1 and 10")
    output_root.mkdir(parents=True, exist_ok=True)
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    selected_sites = queue["sites"][: args.max_sites]
    summary_path = output_root / "summary.json"
    summary: dict[str, Any] = (
        json.loads(summary_path.read_text(encoding="utf-8"))
        if summary_path.exists()
        else {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "queue": str(queue_path),
            "intent_model": "qwen3.5-flash",
            "code_model": "qwen3.5-plus",
            "gpu_jobs": 0,
            "sites": [],
        }
    )
    records = {record["site_id"]: record for record in summary["sites"]}

    for site in selected_sites:
        site_id = site["site_id"]
        site_root = output_root / site_id
        site_root.mkdir(parents=True, exist_ok=True)
        record = records.get(site_id, {
            "site_id": site_id,
            "label": site["label"],
            "start_url": site["start_url"],
            "status": "running_front_half",
            "tools": [],
        })
        if site_id not in records:
            summary["sites"].append(record)
            records[site_id] = record
        write_json(summary_path, summary)

        manifest_path = site_root / "site_manifest.json"
        recon_path = site_root / "recon" / "recon.json"
        if not manifest_path.exists():
            logs = site_root / "queue_logs" / "front_half"
            command = [
                "python", str(PIPELINE_ROOT / "run_site_pipeline.py"),
                site["start_url"],
                "--run-root", str(site_root),
                "--through", "selection",
                "--allow-partial-selection",
                "--max-pages", "8",
                "--proposal-backend", "api",
                "--api-endpoint", "https://yibuapi.com/v1/chat/completions",
                "--api-model", "qwen3.5-flash",
                "--api-key-file", str(key_file),
                "--api-billing-mode", "monthly_quota",
                "--api-max-output-tokens", "8192",
                "--api-max-input-chars", "250000",
                "--api-max-usd", "0.25",
                "--api-timeout-seconds", "300",
            ]
            exit_code = run_logged(
                command,
                cwd=SHARED_ROOT,
                stdout_path=logs / "stdout.txt",
                stderr_path=logs / "stderr.txt",
                timeout_seconds=1200,
            )
            if exit_code != 0 or not manifest_path.exists():
                record.update({
                    "status": "front_half_failed",
                    "front_half_exit_code": exit_code,
                    "verified_tools": 0,
                })
                write_json(summary_path, summary)
                continue

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        recon = json.loads(recon_path.read_text(encoding="utf-8"))
        record["contracts_selected"] = len(manifest.get("tools", []))
        record["candidate_queue_policy"] = (
            "generate_each_screened_candidate_without_waiting_for_ten"
        )
        record["status"] = "generating_code"
        write_json(summary_path, summary)
        tool_records = {item["tool"]: item for item in record.get("tools", [])}

        for tool in manifest["tools"][: args.max_tools_per_site]:
            name = tool["name"]
            attempt_root = site_root / "webwright_attempts" / name / "attempt_1"
            status_path = attempt_root.parent / "status.json"
            if status_path.exists():
                status = json.loads(status_path.read_text(encoding="utf-8"))
                tool_records[name] = status
                record["tools"] = list(tool_records.values())
                continue
            config_path = site_root / "webwright_configs" / f"{name}.yaml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            prompt = code_system_prompt(manifest, tool, recon)
            config = webwright_config(
                system_template=prompt,
                api_log_path=site_root / "api_logs" / f"{name}.json",
                key_file=key_file,
            )
            config_path.write_text(
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            logs = site_root / "queue_logs" / f"code_{name}"
            command = [
                "python", str(PIPELINE_ROOT / "run_webwright_pilot.py"),
                "--task", f"Generate the reusable code tool {name} for {site['label']}.",
                "--task-id", f"{site_id}_{name}_weekend_v1",
                "--start-url", site["start_url"],
                "--output-dir", str(attempt_root),
                "--config", str(config_path),
            ]
            exit_code = run_logged(
                command,
                cwd=SHARED_ROOT,
                stdout_path=logs / "stdout.txt",
                stderr_path=logs / "stderr.txt",
                timeout_seconds=420,
            )
            script_path = attempt_root / "final_script.py"
            errors: list[str] = []
            if exit_code != 0:
                errors.append(f"WebWright process exit {exit_code}")
            if not script_path.is_file():
                errors.append("missing final_script.py")
            else:
                errors.extend(_validate_code(
                    script_path.read_text(encoding="utf-8"),
                    name,
                ))
            status = {
                "tool": name,
                "status": "static_pass" if not errors else "generation_or_static_fail",
                "webwright_exit_code": exit_code,
                "errors": errors,
                "script": str(script_path) if script_path.exists() else None,
                "live_execution": "not_run",
                "independent_verification": "not_run",
                "counts_as_verified": False,
            }
            write_json(status_path, status)
            tool_records[name] = status
            record["tools"] = list(tool_records.values())
            write_json(summary_path, summary)

        record["static_pass_drafts"] = sum(
            item.get("status") == "static_pass" for item in record["tools"]
        )
        record["verified_tools"] = 0
        record["status"] = "code_generation_complete_pending_live_verification"
        write_json(summary_path, summary)

    summary["status"] = "complete"
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary["totals"] = {
        "sites_requested": len(selected_sites),
        "sites_with_ten_contracts": sum(
            record.get("contracts_selected") == 10 for record in summary["sites"]
        ),
        "static_pass_drafts": sum(
            record.get("static_pass_drafts", 0) for record in summary["sites"]
        ),
        "verified_tools": 0,
    }
    write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
