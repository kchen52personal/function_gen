#!/usr/bin/env python3
"""Build deterministic intent, trajectory, and test artifacts for a verified suite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_result(log_path: Path) -> dict[str, Any]:
    prefix = "final result: "
    for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
        if line.startswith(prefix):
            return json.loads(line[len(prefix):])
    raise ValueError(f"{log_path} has no final structured result")


def _argument_phrase(arguments: dict[str, Any]) -> str:
    return ", ".join(
        f"{name}={json.dumps(value, ensure_ascii=False)}"
        for name, value in arguments.items()
    )


def _intents(
    site_name: str,
    tool: dict[str, Any],
    arguments: dict[str, Any],
    split: str,
) -> list[str]:
    goal = tool["goal"].rstrip(".")
    phrase = _argument_phrase(arguments)
    if split == "train":
        return [
            f"On {site_name}, {goal[0].lower() + goal[1:]}. Use {phrase}.",
            f"Please {goal[0].lower() + goal[1:]} with {phrase}.",
            f"{goal}. Parameters: {phrase}.",
        ]
    return [
        f"Could you {goal[0].lower() + goal[1:]}? Use {phrase}.",
        f"I need to {goal[0].lower() + goal[1:]}; set {phrase}.",
    ]


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


TEST_SOURCE = '''"""Contract tests for a generated website tool-suite evaluation bundle."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_exactly_ten_verified_tools() -> None:
    manifest = json.loads((ROOT / "site_manifest.json").read_text(encoding="utf-8"))
    ledger = json.loads((ROOT / "live_verification.json").read_text(encoding="utf-8"))
    assert len(manifest["tools"]) == 10
    assert ledger["verified_tools"] == 10
    assert all(item["status"] == "verified" for item in ledger["tools"].values())


def test_intent_splits_cover_every_tool() -> None:
    manifest = json.loads((ROOT / "site_manifest.json").read_text(encoding="utf-8"))
    names = {tool["name"] for tool in manifest["tools"]}
    train = read_jsonl(ROOT / "intents" / "train.jsonl")
    held = read_jsonl(ROOT / "intents" / "held.jsonl")
    assert len(train) == 30
    assert len(held) == 20
    assert {row["expected_tool"] for row in train} == names
    assert {row["expected_tool"] for row in held} == names
    assert all(row["split"] == "train" for row in train)
    assert all(row["split"] == "held" for row in held)


def test_held_arguments_are_not_default_arguments() -> None:
    ledger = json.loads((ROOT / "live_verification.json").read_text(encoding="utf-8"))
    for record in ledger["tools"].values():
        assert record["default_run"]["arguments"] != record["held_run"]["arguments"]


def test_every_live_trajectory_has_traceable_evidence() -> None:
    trajectories = sorted((ROOT / "trajectories").glob("*.json"))
    assert len(trajectories) == 20
    for path in trajectories:
        item = json.loads(path.read_text(encoding="utf-8"))
        assert item["verifier_outcome"] == "pass"
        assert item["result"]["tool"] == item["chosen_tool"]
        assert item["evidence"]["log"]
        assert len(item["evidence"]["screenshots"]) >= 2
        assert all((ROOT / rel).is_file() for rel in item["evidence"]["screenshots"])
        assert (ROOT / item["evidence"]["log"]).is_file()
'''


def build(site_root: Path) -> dict[str, int]:
    manifest = _read_json(site_root / "site_manifest.json")
    ledger = _read_json(site_root / "live_verification.json")
    tools = {tool["name"]: tool for tool in manifest["tools"]}
    if len(tools) != 10 or ledger.get("verified_tools") != 10:
        raise ValueError("evaluation bundle requires exactly ten verified tools")

    train_records: list[dict[str, Any]] = []
    held_records: list[dict[str, Any]] = []
    trajectory_count = 0
    trajectory_dir = site_root / "trajectories"
    trajectory_dir.mkdir(parents=True, exist_ok=True)

    for tool_name, tool in tools.items():
        verification = ledger["tools"][tool_name]
        if verification.get("status") != "verified":
            raise ValueError(f"{tool_name} is not verified")
        for split, run_key in (("train", "default_run"), ("held", "held_run")):
            run = verification[run_key]
            args = run["arguments"]
            records = train_records if split == "train" else held_records
            for index, text in enumerate(
                _intents(manifest["site_name"], tool, args, split), start=1
            ):
                records.append({
                    "id": f"{tool_name}-{split}-{index}",
                    "site": manifest["site_id"],
                    "intent": text,
                    "expected_tool": tool_name,
                    "expected_arguments": args,
                    "split": split,
                })

            run_path = Path(run["path"])
            absolute_run = site_root / run_path
            screenshots = sorted((absolute_run / "screenshots").glob("*.png"))
            result = _read_result(absolute_run / "final_script_log.txt")
            trajectory = {
                "id": f"{tool_name}-{split}-live",
                "site": manifest["site_id"],
                "intent_id": f"{tool_name}-{split}-1",
                "intent": records[-len(_intents(manifest["site_name"], tool, args, split))]["intent"],
                "chosen_tool": tool_name,
                "parsed_arguments": args,
                "run_path": run["path"],
                "result": result,
                "verifier_outcome": "pass",
                "evidence": {
                    "log": str(run_path / "final_script_log.txt"),
                    "screenshots": [
                        str(path.relative_to(site_root)) for path in screenshots
                    ],
                },
            }
            (trajectory_dir / f"{tool_name}_{split}.json").write_text(
                json.dumps(trajectory, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            trajectory_count += 1

    _write_jsonl(site_root / "intents" / "train.jsonl", train_records)
    _write_jsonl(site_root / "intents" / "held.jsonl", held_records)
    tests_dir = site_root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_bundle.py").write_text(TEST_SOURCE, encoding="utf-8")
    summary = {
        "tools": len(tools),
        "train_intents": len(train_records),
        "held_intents": len(held_records),
        "live_trajectories": trajectory_count,
    }
    (site_root / "eval_bundle_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("site_root", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.site_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
