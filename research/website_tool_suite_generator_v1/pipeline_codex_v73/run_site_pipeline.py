#!/usr/bin/env python3
"""Run the bounded website-to-tool pipeline as a resumable state machine."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH",
    str(PROJECT_ROOT / ".playwright-browsers"),
)

from local_propose import DEFAULT_MODEL, propose
from api_propose import propose_api
from reconnaissance import reconnaissance
from select_candidates import SelectionError, select
from validate_and_scaffold import plan_text, validate


SHARED_ROOT = Path("/shared/hans").resolve()
STAGE_INDEX = {"recon": 0, "proposal": 1, "selection": 2, "scaffold": 3}


def _inside_shared(path: Path) -> Path:
    resolved = path.resolve()
    if resolved != SHARED_ROOT and SHARED_ROOT not in resolved.parents:
        raise ValueError("run-root must be inside /shared/hans")
    return resolved


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _state(run_root: Path, start_url: str) -> dict[str, Any]:
    paths = {
        "recon": run_root / "recon" / "recon.json",
        "proposal": run_root / "candidates" / "proposal.json",
        "selection_audit": run_root / "candidates" / "selection_audit.json",
        "manifest": run_root / "site_manifest.json",
        "manifest_audit": run_root / "manifest_audit.json",
    }
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "start_url": start_url,
        "run_root": str(run_root),
        "artifacts": {
            name: {
                "path": str(path),
                "sha256": _sha256(path) if path.exists() else None,
            }
            for name, path in paths.items()
        },
        "completed": {
            "recon": paths["recon"].exists(),
            "proposal": paths["proposal"].exists(),
            "selection": paths["manifest"].exists(),
            "scaffold": paths["manifest_audit"].exists(),
        },
    }


def _record_state(run_root: Path, start_url: str) -> dict[str, Any]:
    state = _state(run_root, start_url)
    _write_json(run_root / "pipeline_state.json", state)
    return state


def _scaffold(manifest: dict[str, Any], run_root: Path) -> dict[str, Any]:
    audit = validate(manifest)
    _write_json(run_root / "manifest_audit.json", audit)
    if audit["errors"]:
        raise ValueError("manifest failed static validation")
    for tool in manifest["tools"]:
        workspace = run_root / "tools" / tool["name"]
        (workspace / "final_runs").mkdir(parents=True, exist_ok=True)
        plan = workspace / "plan.md"
        if not plan.exists():
            plan.write_text(plan_text(manifest, tool), encoding="utf-8")
    return audit


def run(args: argparse.Namespace) -> dict[str, Any]:
    run_root = _inside_shared(args.run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    through = STAGE_INDEX[args.through]
    recon_path = run_root / "recon" / "recon.json"
    proposal_path = run_root / "candidates" / "proposal.json"
    audit_path = run_root / "candidates" / "selection_audit.json"
    manifest_path = run_root / "site_manifest.json"
    provenance_path = run_root / "pipeline_provenance.json"
    provenance = (
        json.loads(provenance_path.read_text(encoding="utf-8"))
        if provenance_path.exists()
        else {}
    )

    _record_state(run_root, args.start_url)
    if through >= STAGE_INDEX["recon"] and not recon_path.exists():
        asyncio.run(reconnaissance(args.start_url, recon_path.parent, args.max_pages))
        provenance["recon"] = {"sha256": _sha256(recon_path)}
        _write_json(provenance_path, provenance)
        _record_state(run_root, args.start_url)

    if through >= STAGE_INDEX["proposal"] and not proposal_path.exists():
        if args.proposal_backend == "local_gpu":
            if args.approved_physical_gpu is None:
                raise ValueError(
                    "local GPU proposal requires --approved-physical-gpu and the scoped "
                    "WEBSITE_TOOL_GPU_APPROVED environment variables"
                )
            propose(
                recon_path,
                proposal_path,
                args.model,
                args.max_new_tokens,
                args.max_images,
                args.max_image_pixels,
                args.max_input_tokens,
                args.approved_physical_gpu,
                args.max_gpu_memory_gib,
            )
        else:
            propose_api(
                recon_path,
                proposal_path,
                endpoint=args.api_endpoint,
                model=args.api_model,
                key_file=args.api_key_file,
                max_output_tokens=args.api_max_output_tokens,
                max_input_chars=args.api_max_input_chars,
                input_usd_per_million=args.api_input_usd_per_million,
                output_usd_per_million=args.api_output_usd_per_million,
                max_usd=args.api_max_usd,
                timeout_seconds=args.api_timeout_seconds,
                billing_mode=args.api_billing_mode,
            )
        provenance["proposal"] = {
            "sha256": _sha256(proposal_path),
            "recon_sha256": _sha256(recon_path),
            "backend": args.proposal_backend,
            "model": (
                str(args.model)
                if args.proposal_backend == "local_gpu"
                else args.api_model
            ),
            "max_images": args.max_images,
            "max_image_pixels": args.max_image_pixels,
            "max_input_tokens": args.max_input_tokens,
        }
        _write_json(provenance_path, provenance)
        _record_state(run_root, args.start_url)

    if through >= STAGE_INDEX["selection"] and not manifest_path.exists():
        proposal_provenance = provenance.get("proposal", {})
        if proposal_provenance.get("sha256") != _sha256(proposal_path):
            raise ValueError("proposal artifact does not match recorded provenance")
        if proposal_provenance.get("recon_sha256") != _sha256(recon_path):
            raise ValueError("proposal was generated from a different recon artifact")
        proposal_data = json.loads(proposal_path.read_text(encoding="utf-8"))
        recon_data = json.loads(recon_path.read_text(encoding="utf-8"))
        try:
            manifest, audit = select(
                proposal_data,
                recon_data,
                require_exact_count=not args.allow_partial_selection,
            )
        except SelectionError as error:
            _write_json(audit_path, error.audit)
            _record_state(run_root, args.start_url)
            raise
        _write_json(audit_path, audit)
        _write_json(manifest_path, manifest)
        provenance["selection"] = {
            "manifest_sha256": _sha256(manifest_path),
            "proposal_sha256": _sha256(proposal_path),
            "recon_sha256": _sha256(recon_path),
        }
        _write_json(provenance_path, provenance)
        _record_state(run_root, args.start_url)

    if through >= STAGE_INDEX["scaffold"] and not (run_root / "manifest_audit.json").exists():
        selection_provenance = provenance.get("selection", {})
        if selection_provenance.get("manifest_sha256") != _sha256(manifest_path):
            raise ValueError("manifest artifact does not match recorded provenance")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("tool_count") != 10:
            raise ValueError(
                "partial candidate queues must proceed directly to per-candidate "
                "generation/verification; they cannot use the legacy ten-tool scaffold"
            )
        _scaffold(manifest, run_root)
        _record_state(run_root, args.start_url)

    return _record_state(run_root, args.start_url)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("start_url")
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument(
        "--through",
        choices=tuple(STAGE_INDEX),
        default="scaffold",
    )
    parser.add_argument("--max-pages", type=int, default=12)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--max-new-tokens", type=int, default=8192)
    parser.add_argument("--max-images", type=int, default=8)
    parser.add_argument("--max-image-pixels", type=int, default=500_000)
    parser.add_argument("--max-input-tokens", type=int, default=36_000)
    parser.add_argument("--approved-physical-gpu", type=int)
    parser.add_argument("--max-gpu-memory-gib", type=int, default=24)
    parser.add_argument(
        "--proposal-backend",
        choices=("local_gpu", "api"),
        default="local_gpu",
    )
    parser.add_argument("--api-endpoint", default="")
    parser.add_argument("--api-model", default="")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--api-max-output-tokens", type=int, default=6144)
    parser.add_argument("--api-max-input-chars", type=int, default=250_000)
    parser.add_argument(
        "--api-billing-mode",
        choices=("token_rates", "monthly_quota"),
        default="token_rates",
    )
    # Negative defaults make API mode fail closed until the operator records
    # the provider's actual rates; otherwise the spend ledger would lie.
    parser.add_argument("--api-input-usd-per-million", type=float)
    parser.add_argument("--api-output-usd-per-million", type=float)
    parser.add_argument("--api-max-usd", type=float, default=0.25)
    parser.add_argument("--api-timeout-seconds", type=int, default=300)
    parser.add_argument(
        "--allow-partial-selection",
        action="store_true",
        help=(
            "emit every valid distinct candidate without requiring ten; intended for "
            "candidate-by-candidate generation and verification orchestrators"
        ),
    )
    args = parser.parse_args()
    try:
        result = run(args)
    except (SelectionError, ValueError) as error:
        print(json.dumps({"status": "stopped", "error": str(error)}, indent=2))
        raise SystemExit(2) from error
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
