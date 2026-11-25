#!/usr/bin/env python3
"""Sequentially propose and select ten-tool suites for preserved site recons."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from api_propose import propose_api
from select_candidates import SelectionError, select


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recon-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--site", action="append", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--max-input-chars", type=int, default=200_000)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()

    summary: dict[str, Any] = {
        "requested_model": args.model,
        "sites": [],
        "policy": "one proposal call per site, no retries, unchanged local selector",
    }
    summary_path = args.output_root / "proposal_selection_summary.json"
    for site_id in args.site:
        recon_path = args.recon_root / site_id / "recon" / "recon.json"
        site_root = args.output_root / site_id
        proposal_path = site_root / "candidates" / "proposal.json"
        audit_path = site_root / "candidates" / "selection_audit.json"
        manifest_path = site_root / "site_manifest.json"
        record: dict[str, Any] = {
            "site_id": site_id,
            "recon": str(recon_path),
            "proposal": str(proposal_path),
            "manifest": str(manifest_path),
        }
        try:
            proposal = propose_api(
                recon_path,
                proposal_path,
                endpoint=args.endpoint,
                model=args.model,
                key_file=args.api_key_file,
                max_output_tokens=args.max_output_tokens,
                max_input_chars=args.max_input_chars,
                input_usd_per_million=None,
                output_usd_per_million=None,
                max_usd=0.25,
                timeout_seconds=args.timeout_seconds,
                billing_mode="monthly_quota",
            )
            recon = json.loads(recon_path.read_text(encoding="utf-8"))
            try:
                manifest, audit = select(proposal, recon)
            except SelectionError as error:
                write_json(audit_path, error.audit)
                record.update({
                    "status": "rejected_selection",
                    "input_candidates": error.audit["input_candidates"],
                    "selected": error.audit["selected"],
                    "error": str(error),
                    "usage": proposal.get("generation", {}),
                })
            else:
                write_json(audit_path, audit)
                write_json(manifest_path, manifest)
                record.update({
                    "status": "selected_ten_contracts",
                    "input_candidates": len(proposal.get("candidates", [])),
                    "selected": len(manifest["tools"]),
                    "tools": [tool["name"] for tool in manifest["tools"]],
                    "usage": proposal.get("generation", {}),
                })
        except Exception as error:
            record.update({
                "status": "rejected_api_or_parse",
                "error_type": type(error).__name__,
                "error": str(error)[:1000],
            })
        summary["sites"].append(record)
        write_json(summary_path, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
