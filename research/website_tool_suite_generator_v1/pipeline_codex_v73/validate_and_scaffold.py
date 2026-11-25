#!/usr/bin/env python3
"""Validate a ten-tool site manifest and scaffold WebWright tool workspaces."""

from __future__ import annotations

import argparse
from itertools import combinations
import json
from pathlib import Path
import re


REQUIRED_TOOL_FIELDS = {
    "name", "effect_key", "goal", "arguments", "returns", "boundary",
    "success_contract", "popularity_proxy", "goal_class",
    "output_object_type", "choose_when", "exclude_when"
}
STOP = {"a", "an", "and", "for", "from", "get", "list", "of", "on", "the", "to"}


def tokens(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", text.casefold()) if token not in STOP
    }


def similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = tokens(left), tokens(right)
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 1.0


def plan_text(site: dict, tool: dict) -> str:
    parameters = []
    for argument in tool["arguments"]:
        parameters.append(
            f"| {argument['name']} | {argument['type']} | tool contract | "
            f"`{argument['default']}` | {argument['constraints']} |"
        )
    rows = "\n".join(parameters)
    return f"""# Task

Create and verify the parameterized WebWright tool `{tool['name']}` for
{site['site_name']}.

Goal: {tool['goal']}

# Parameters

| name | type | source | default | allowed / format |
|---|---|---|---|---|
{rows}

# Critical Points

- [ ] CP1: Open `{site['start_url']}` in fresh Playwright Firefox.
- [ ] CP2: Apply every parameter through the site's visible controls or a verified site route.
- [ ] CP3: Reach this postcondition: {tool['success_contract']}
- [ ] CP4: Return this structured object: {tool['returns']}.
- [ ] CP5: Save bounded screenshots and a step log proving CP1–CP4.
- [ ] CP6: Pass one default and one held-argument run from fresh browser contexts.

# Boundary

{tool['boundary']}
"""


def validate(manifest: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    tools = manifest.get("tools", [])
    if len(tools) != 10:
        errors.append(f"expected exactly 10 tools; found {len(tools)}")

    for index, tool in enumerate(tools):
        missing = sorted(REQUIRED_TOOL_FIELDS - set(tool))
        if missing:
            errors.append(f"tool {index} missing fields: {', '.join(missing)}")
        if len(tool.get("arguments", [])) == 0:
            errors.append(f"tool {tool.get('name', index)} has no parameterized arguments")
        proxy = tool.get("popularity_proxy", {})
        if not proxy.get("signals"):
            errors.append(f"tool {tool.get('name', index)} has no popularity-proxy signals")
        if proxy.get("claim") not in {"high", "medium"}:
            errors.append(
                f"tool {tool.get('name', index)} popularity-proxy claim must be high or medium"
            )

    for field in (
        "name", "effect_key", "goal", "returns", "goal_class", "output_object_type"
    ):
        values = [json.dumps(tool.get(field), sort_keys=True) for tool in tools]
        if len(values) != len(set(values)):
            errors.append(f"duplicate {field}")

    pairwise = []
    for left, right in combinations(tools, 2):
        score = similarity(left["goal"], right["goal"])
        record = {
            "left": left["name"],
            "right": right["name"],
            "goal_token_jaccard": round(score, 3),
            "same_effect": left["effect_key"] == right["effect_key"],
            "same_goal_class": left["goal_class"] == right["goal_class"],
            "same_output_object": left["output_object_type"] == right["output_object_type"],
            "same_returns": left["returns"] == right["returns"],
            "why_distinct": (
                f"Choose {left['name']} when {left['choose_when']}; choose "
                f"{right['name']} when {right['choose_when']}. Terminal objects are "
                f"{left['output_object_type']} vs {right['output_object_type']}."
            ),
        }
        if record["same_effect"] or record["same_goal_class"] or record["same_output_object"]:
            errors.append(
                f"behavioral duplicate: {left['name']} vs {right['name']}"
            )
        if score >= 0.6:
            warnings.append(
                f"high goal-text overlap: {left['name']} vs {right['name']} ({score:.3f})"
            )
        pairwise.append(record)

    return {
        "status": "pass" if not errors else "fail",
        "tool_count": len(tools),
        "pair_count": len(pairwise),
        "errors": errors,
        "warnings": warnings,
        "pairwise": pairwise,
        "caveat": (
            "Static validation rejects duplicate contracts and flags high lexical overlap. "
            "Popularity is inferred from visible product-placement proxies, not usage telemetry. "
            "Behavioral distinctness still requires contrastive intents and live execution."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    audit = validate(manifest)
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "manifest_audit.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if audit["errors"]:
        print(json.dumps(audit, indent=2, ensure_ascii=False))
        raise SystemExit(1)

    for tool in manifest["tools"]:
        workspace = args.output_root / "tools" / tool["name"]
        (workspace / "final_runs").mkdir(parents=True, exist_ok=True)
        plan = workspace / "plan.md"
        if not plan.exists():
            plan.write_text(plan_text(manifest, tool), encoding="utf-8")

    print(json.dumps({
        "status": audit["status"], "tool_count": audit["tool_count"],
        "pair_count": audit["pair_count"], "warnings": audit["warnings"],
        "scaffolded_root": str(args.output_root / "tools"),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

