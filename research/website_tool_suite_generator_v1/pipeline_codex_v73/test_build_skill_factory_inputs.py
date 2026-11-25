from __future__ import annotations

import json
from pathlib import Path

from build_skill_factory_inputs import build


SITE_ROOT = Path(__file__).resolve().parents[1] / "netease_v1"


def test_builds_twenty_webwright_inputs(tmp_path: Path) -> None:
    output = tmp_path / "skill_factory_runs"
    summary = build(SITE_ROOT, output)
    assert summary["status"] == "ready_for_distillation"
    assert summary["tools"] == 10
    assert summary["runs"] == 20
    assert len(list(output.glob("*/task.json"))) == 20
    assert len(list(output.glob("*/final_script.py"))) == 20
    assert len(list(output.glob("*/agent_response.json"))) == 20
    assert len(json.loads((output / "golds.json").read_text(encoding="utf-8"))) == 20


def test_each_tool_has_default_and_held_inputs(tmp_path: Path) -> None:
    output = tmp_path / "skill_factory_runs"
    summary = build(SITE_ROOT, output)
    splits: dict[str, set[str]] = {}
    for record in summary["records"]:
        splits.setdefault(record["tool"], set()).add(record["split"])
    assert len(splits) == 10
    assert all(value == {"default", "held"} for value in splits.values())
