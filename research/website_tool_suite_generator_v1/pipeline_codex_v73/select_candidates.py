#!/usr/bin/env python3
"""Select ten behaviorally distinct candidates from a model proposal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlparse


REQUIRED = {
    "name", "effect_key", "goal_class", "output_object_type", "choose_when",
    "exclude_when", "goal", "arguments", "test_cases", "returns", "return_evidence", "boundary",
    "success_contract", "popularity_proxy", "evidence",
}
STOP = {
    "a", "an", "and", "data", "for", "from", "get", "information", "list",
    "navigate", "of", "on", "page", "record", "records", "retrieve", "structured",
    "the", "to", "view",
}


class SelectionError(ValueError):
    """Raised when fewer than ten candidates survive, with an auditable report."""

    def __init__(self, message: str, audit: dict[str, Any]):
        super().__init__(message)
        self.audit = audit


def _tokens(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", text.casefold())
        if token not in STOP
    }


def _similarity(left: str, right: str) -> float:
    one, two = _tokens(left), _tokens(right)
    union = one | two
    return len(one & two) / len(union) if union else 1.0


def _rank(candidate: dict[str, Any]) -> tuple[int, int, int, str]:
    proxy = candidate["popularity_proxy"]
    claim = 2 if proxy.get("claim") == "high" else 1
    signals = len(proxy.get("signals", []))
    priority = int(candidate.get("priority", 999))
    return (-claim, -signals, priority, candidate["name"])


def _normalized_path(path: str) -> str:
    value = re.sub(r"/ref=[^/]+/?$", "", path.rstrip("/"))
    return value or "/"


def _url_matches(claimed: str, target: str) -> bool:
    left, right = urlparse(claimed), urlparse(target)
    if (
        left.scheme.casefold() != right.scheme.casefold()
        or (left.hostname or "").casefold() != (right.hostname or "").casefold()
        or _normalized_path(left.path) != _normalized_path(right.path)
    ):
        return False
    target_query = set(parse_qsl(right.query, keep_blank_values=True))
    claimed_query = set(parse_qsl(left.query, keep_blank_values=True))
    return claimed_query <= target_query


def _evidence_url_supported(claimed: str, observed_item: dict[str, Any]) -> bool:
    source_url = str(observed_item["page_url"])
    if _url_matches(claimed, source_url):
        return True
    href = str(observed_item.get("record", {}).get("href", "")).strip()
    if href and not href.startswith(("javascript:", "mailto:", "tel:", "data:")):
        return _url_matches(claimed, urljoin(source_url, href))
    return False


def _recon_observations(recon: dict[str, Any]) -> dict[str, Any]:
    urls: set[str] = set()
    text_values: list[str] = []
    evidence: dict[str, dict[str, Any]] = {}

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"requested_url", "final_url", "url"} and isinstance(item, str):
                    urls.add(item)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, (str, int, float, bool)):
            text_values.append(str(value).casefold())

    collect(recon)
    for page_index, page in enumerate(recon.get("pages", [])):
        for surface_index, frame in enumerate(page.get("frames", [])):
            page_url = str(frame.get("url") or page.get("final_url") or "")
            for source, kind in (
                ("headings", "heading"),
                ("links", "link"),
                ("actions", "action"),
                ("fields", "field"),
                ("forms", "form"),
                ("content", "content"),
            ):
                for index, item in enumerate(frame.get(source, [])):
                    evidence_id = (
                        f"p{page_index:02d}.s{surface_index:02d}."
                        f"{kind}.{index:03d}"
                    )
                    value = next((
                        str(item.get(key, "")).strip()
                        for key in (
                            "text", "title", "aria_label", "name", "href", "type", "role"
                        )
                        if str(item.get(key, "")).strip()
                    ), "")
                    evidence[evidence_id] = {
                        "page_url": page_url,
                        "value": value,
                        "record": item,
                    }
    return {
        "urls": urls,
        "text": "\n".join(text_values),
        "evidence": evidence,
    }


def integer_range_coverage_errors(candidate: dict[str, Any]) -> list[str]:
    """Fail closed when a bounded integer contract cannot drive boundary tests."""
    errors = []
    for spec in candidate.get('arguments', []):
        if not isinstance(spec, dict) or str(spec.get('type', '')).casefold().strip() not in {'int', 'integer'}:
            continue
        text = str(spec.get('constraints', ''))
        bounded = re.search(r'\bbetween\s+-?\d+\s+(?:and|to)\s+-?\d+\b|'
                            r'(?<!\d)-?\d+\s*(?:-|–|—|\.\.|\bto\b|\bthrough\b)\s*-?\d+\b|'
                            r'\b(?:min(?:imum)?|at least)\s*\d+.{0,50}\b(?:max(?:imum)?|at most)\s*\d+',
                            text, re.I)
        if not bounded:
            continue
        canonical = re.search(r'\bbetween\s+(\d+)\s+and\s+(\d+)\s+inclusive\b', text, re.I)
        name = str(spec.get('name', '?'))
        if not canonical or re.search(r'\b(?:exclusive|excluding|except|not between)\b', text, re.I):
            errors.append(f'argument {name} has an unsupported or ambiguous bounded integer range; '
                          'use "between X and Y inclusive"')
            continue
        low, high = map(int, canonical.groups())
        if low > high or high > 100:
            errors.append(f'argument {name} bounded range is outside automated boundary coverage (0–100)')
            continue
        cases = candidate.get('test_cases')
        if isinstance(cases, dict):
            for label in ('default', 'held'):
                case = cases.get(label)
                value = case.get(name) if isinstance(case, dict) else None
                if type(value) is int and not low <= value <= high:
                    errors.append(f'test_cases.{label}.{name} is outside the declared inclusive range')
    return errors


def goal_schema_mismatches(candidate: dict[str, Any]) -> list[str]:
    """Catch narrow, explicit output promises missing from a flat schema.

    This is not a general semantic proof; nested schemas and ambiguous wording
    are deliberately left to later review rather than guessed here.
    """
    returns = candidate.get('returns')
    if not isinstance(returns, dict) or not returns:
        return []
    goal = str(candidate.get('goal', '')).casefold()
    contract = ' '.join(str(candidate.get(key, '')).casefold()
                        for key in ('goal', 'success_contract', 'boundary'))
    errors = []
    arguments = candidate.get('arguments', [])
    # The recon inventory numbers arbitrary observations; it does not make
    # those numbers user-visible positions in a meaningful site collection.
    # Reject only this narrow vague bucket/index pattern. A site could truly
    # have a collection named "content samples" or "other links", so this
    # conservative screen may reject useful tasks and cannot prove semantics.
    evidence = candidate.get('proposal_evidence') or candidate.get('evidence') or {}
    evidence_id = str(evidence.get('evidence_id', '')) if isinstance(evidence, dict) else ''
    if (re.search(r'\b(?:content\s+samples?|other\s+links?)\b', goal)
            and re.search(r'\.s\d+\.(?:content|link)\.\d+$', evidence_id, re.I)
            and isinstance(arguments, list)
            and any(isinstance(arg, dict) and re.search(r'(?:^|_)index$', str(arg.get('name', '')), re.I)
                    for arg in arguments)):
        errors.append('indexed recon content/other-link bucket lacks a defined user-visible collection and order')
    if isinstance(arguments, list):
        for argument in arguments:
            if not isinstance(argument, dict):
                continue
            text = str(argument.get('constraints', '')).casefold()
            if re.search(r'\b(?:one\s+of|match)\b.{0,80}\b(?:observed|available|listed)\b.{0,80}'
                         r'\b(?:such\s+as|for\s+example|e\.g\.)', text):
                errors.append(f"argument {argument.get('name', '?')} uses examples for an open observed-option domain; coverage is not bounded")
    if ('field_type' in {str(field).casefold() for field in returns}
            and re.search(r'\bsection\s+heading\s+as\s+(?:the\s+)?field\s+type\b', contract)):
        errors.append('contract redefines a requested field type as an unrelated section heading')
    source_evidence = candidate.get('return_evidence') or candidate.get('proposal_return_evidence') or {}
    if isinstance(source_evidence, dict):
        for field in returns:
            evidence = source_evidence.get(field)
            label = evidence.get('evidence_id') if isinstance(evidence, dict) else None
            if (re.search(r'(?:^|_)evidence_id$', str(field), re.I)
                    and isinstance(label, str)
                    and re.fullmatch(r'p\d+\.s\d+\.heading\.\d+', label)):
                errors.append('internal reconnaissance heading evidence ID is presented as a website output')
    if re.search(r"\b(?:other_links|primary_links)['\"]?\s+section\b|"
                 r"\bsection\s+['\"]?(?:other_links|primary_links)\b", contract):
        errors.append('internal reconnaissance link bucket is described as a page section')
    if any(str(kind).casefold().replace(' ', '') in {'object', 'list', 'list[object]'}
           for kind in returns.values()):
        return errors
    names = [str(name).casefold() for name in returns]
    if (re.search(r'\bsample\s+(?:content|text|facts?|items?|sentences?|paragraphs?)\b', goal)
            and not re.search(r'\b(?:first|last|top)\s+(?:(?:\d+|one|two|three|four|five|ten)\s+)?'
                              r'(?:words?|characters?|items?|sentences?|facts?|paragraphs?|lines?)\b', contract)):
        errors.append('sample content has no explicit first/last item or count; exact independent comparison is ambiguous')
    if ('headline_text' in names and re.search(r'\bheadlines?\b', goal)
            and not re.search(r"\b(?:full|entire|whole)\s+(?:news\s+)?(?:list\s+item|item|headline|sentence)\b"
                              r"|\b(?:first|bold|primary)\s+(?:article\s+)?link(?:'s)?\s+(?:text|label)\b", contract)):
        errors.append('headline text does not specify full item versus link text; exact independent comparison is ambiguous')
    if (re.search(r'(?:,\s*|\band\s+)(?:an?\s+)?descriptions?\b', goal)
            and not any(re.search(r'(?:^|_)(?:description|summary|snippet|purpose|content|body|details|text)(?:_|$)', name)
                        for name in names)):
        errors.append('goal explicitly promises a description but return schema has no description-like field')
    if re.search(r'\bportal\s+(?:url|link|href)\b', goal):
        has_portal = any(('portal' in name and re.search(r'(?:url|link|href)', name))
                         or name in {'url', 'link', 'href'} for name in names)
        if not has_portal:
            errors.append('goal explicitly promises a portal URL but return schema has no portal link field')
    # A list of labels is not a list of links. This conservative name check
    # can reject a URL-bearing field with an ambiguous name; nested schemas
    # above are deliberately excluded because their fields are not specified.
    if (re.search(r'\b(?:retrieve|return|extract|list|show|get)\b.{0,150}\blinks?\b', goal)
            and not re.search(r'\blink\s+(?:texts?|labels?|names?|titles?)\b', goal)
            and not any(re.search(r'(?:^|_)(?:urls?|hrefs?|links?)(?:_|$)', name)
                        for name in names)):
        errors.append('goal explicitly promises links but return schema has no URL/link field')
    return errors


def _valid(
    candidate: dict[str, Any],
    recon_observations: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED - set(candidate))
    if missing:
        errors.append(f"missing fields: {', '.join(missing)}")
        return errors
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", str(candidate["name"])):
        errors.append("invalid snake_case name")
    if not candidate["arguments"]:
        errors.append("no parameterized arguments")
    argument_names: list[str] = []
    argument_types: dict[str, str] = {}
    argument_defaults: dict[str, Any] = {}
    for index, argument in enumerate(candidate["arguments"]):
        if not {"name", "type", "default", "constraints"} <= set(argument):
            errors.append(f"argument {index} lacks contract fields")
            continue
        name = argument.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            errors.append(f"argument {index} has invalid name")
            continue
        if name in argument_types:
            errors.append(f"duplicate argument name: {name}")
            continue
        kind = str(argument.get("type", "")).casefold().strip()
        if kind not in {"str", "string", "int", "integer", "bool", "boolean", "float", "number"}:
            errors.append(f"argument {name} has unsupported type")
            continue
        if not str(argument.get("constraints", "")).strip():
            errors.append(f"argument {name} has empty constraints")
        if re.search(r"\b(?:ignored?|unused|no effect)\b",
                     str(argument.get("constraints", "")), re.I):
            errors.append(f"argument {name} is declared ignored or unused")
        argument_names.append(name)
        argument_types[name] = kind
        argument_defaults[name] = argument.get("default")
    cases = candidate.get("test_cases")
    if not isinstance(cases, dict) or set(cases) != {"default", "held"}:
        errors.append("test_cases must contain exactly default and held")
    else:
        expected_names = set(argument_names)
        typed_cases: dict[str, dict[str, Any]] = {}
        for label in ("default", "held"):
            case = cases.get(label)
            if not isinstance(case, dict) or set(case) != expected_names:
                errors.append(f"test_cases.{label} must contain every argument and no others")
                continue
            typed_cases[label] = case
            for name, value in case.items():
                kind = argument_types[name]
                valid_type = (
                    (kind in {"str", "string"} and isinstance(value, str) and bool(value.strip()))
                    or (kind in {"int", "integer"} and type(value) is int)
                    or (kind in {"float", "number"} and type(value) in {int, float})
                    or (kind in {"bool", "boolean"} and type(value) is bool)
                )
                if not valid_type:
                    errors.append(f"test_cases.{label}.{name} has wrong type")
        if "default" in typed_cases and any(
            typed_cases["default"].get(name) != argument_defaults.get(name)
            for name in expected_names
        ):
            errors.append("test_cases.default must exactly match argument defaults")
        if set(typed_cases) == {"default", "held"} and typed_cases["default"] == typed_cases["held"]:
            errors.append("test_cases.held must differ from default")
    proxy = candidate["popularity_proxy"]
    if proxy.get("claim") not in {"high", "medium"}:
        errors.append("popularity claim is not high or medium")
    if not proxy.get("signals"):
        errors.append("no popularity signals")
    elif recon_observations is not None:
        for signal in proxy["signals"]:
            if signal not in recon_observations["evidence"]:
                errors.append(f"popularity evidence ID {signal} was not observed")
    evidence = candidate["evidence"]
    if not evidence.get("page_url") or not evidence.get("evidence_id"):
        errors.append("no observable reconnaissance evidence")
    elif recon_observations is not None:
        evidence_id = evidence["evidence_id"]
        observed_item = recon_observations["evidence"].get(evidence_id)
        if observed_item is None:
            errors.append(f"candidate evidence ID {evidence_id} was not observed")
        elif not _evidence_url_supported(str(evidence["page_url"]), observed_item):
            errors.append(
                "candidate evidence URL matches neither the evidence page nor its href"
            )
    returns = candidate["returns"]
    field_evidence = candidate["return_evidence"]
    if not isinstance(returns, dict) or not returns:
        errors.append("returns must be a non-empty field schema")
    elif any(
        str(kind).casefold().replace(" ", "") not in {
            "str", "int", "float", "bool", "object", "list", "list[object]", "list[str]"
        }
        for kind in returns.values()
    ):
        errors.append("returns contains an unsupported field type")
    if not isinstance(field_evidence, dict):
        errors.append("return_evidence must map every return field to evidence")
    elif isinstance(returns, dict):
        missing_evidence = sorted(set(returns) - set(field_evidence))
        extra_evidence = sorted(set(field_evidence) - set(returns))
        if missing_evidence:
            errors.append(
                "return fields lack evidence: " + ", ".join(missing_evidence)
            )
        if extra_evidence:
            errors.append(
                "return evidence has no matching field: " + ", ".join(extra_evidence)
            )
        for field in sorted(set(returns) & set(field_evidence)):
            item = field_evidence[field]
            if not isinstance(item, dict):
                errors.append(f"return evidence for {field} is not an object")
                continue
            evidence_id = str(item.get("evidence_id", "")).strip()
            if not evidence_id:
                errors.append(f"return evidence for {field} lacks evidence ID")
                continue
            if recon_observations is not None:
                if evidence_id not in recon_observations["evidence"]:
                    errors.append(f"return evidence ID for {field} was not observed")
    goal = str(candidate["goal"]).casefold()
    success = str(candidate["success_contract"]).casefold()
    output_type = str(candidate["output_object_type"]).casefold()
    returned = str(candidate["returns"]).strip().casefold()
    errors.extend(goal_schema_mismatches(candidate))
    errors.extend(integer_range_coverage_errors(candidate))
    if "navigate to" in goal or "browser navigates" in success:
        errors.append("navigation-only primitive, not a completed user functionality")
    if output_type.endswith("page_url") or re.fullmatch(r"https?://\S+", returned):
        errors.append("URL-only output, not extracted structured site data")
    if re.search(r"\b(?:random|randomly|shuffle|lottery|nondeterministic)\b",
                 " ".join((str(candidate["name"]), goal, success, output_type)), re.I):
        errors.append("nondeterministic functionality cannot be blindly replicated")
    if output_type.startswith(("list[", "list_")) and isinstance(returns, dict) and not any(
        str(kind).casefold().replace(" ", "").startswith("list")
        for kind in returns.values()
    ):
        errors.append("list output object has no list-valued return field")
    return errors


def select(
    proposal: dict[str, Any],
    recon: dict[str, Any] | None = None,
    *,
    require_exact_count: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    seen_names: set[str] = set()
    seen_effects: set[str] = set()
    seen_goals: set[str] = set()
    seen_objects: set[str] = set()
    normalizations: list[dict[str, str]] = []

    recon_observations = _recon_observations(recon) if recon is not None else None
    for raw_candidate in sorted(proposal.get("candidates", []), key=_rank):
        candidate = dict(raw_candidate)
        # goal_class is a deduplication label, not a factual capability claim.
        # Derive it from the required behavior key when the model omits only
        # this label; record the normalization so it is never silent.
        if not str(candidate.get("goal_class", "")).strip() and isinstance(
            candidate.get("effect_key"), str
        ):
            derived = re.sub(r"[^a-z0-9]+", "_", candidate["effect_key"].casefold()).strip("_")
            if derived:
                candidate["goal_class"] = "derived_" + derived
                normalizations.append({
                    "name": str(candidate.get("name", "?")),
                    "field": "goal_class",
                    "value": candidate["goal_class"],
                    "source": "effect_key",
                })
        errors = _valid(candidate, recon_observations)
        if errors:
            rejected.append({"name": str(candidate.get("name", "?")), "reason": "; ".join(errors)})
            continue
        duplicates = []
        if candidate["name"] in seen_names:
            duplicates.append("name")
        if candidate["effect_key"] in seen_effects:
            duplicates.append("effect_key")
        if candidate["goal_class"] in seen_goals:
            duplicates.append("goal_class")
        if candidate["output_object_type"] in seen_objects:
            duplicates.append("output_object_type")
        overlap = [
            prior["name"] for prior in accepted
            if _similarity(candidate["goal"], prior["goal"]) >= 0.6
        ]
        if duplicates or overlap:
            reason = []
            if duplicates:
                reason.append(f"duplicate {', '.join(duplicates)}")
            if overlap:
                reason.append(f"high goal overlap with {', '.join(overlap)}")
            rejected.append({"name": candidate["name"], "reason": "; ".join(reason)})
            continue
        if len(accepted) == 10 and require_exact_count:
            rejected.append({
                "name": candidate["name"],
                "reason": "not selected: lower-ranked than the ten-candidate capacity",
            })
            continue
        accepted.append(candidate)
        seen_names.add(candidate["name"])
        seen_effects.add(candidate["effect_key"])
        seen_goals.add(candidate["goal_class"])
        seen_objects.add(candidate["output_object_type"])

    if len(accepted) != 10 and require_exact_count:
        audit = {
            "status": "rejected_insufficient_distinct_candidates",
            "input_candidates": len(proposal.get("candidates", [])),
            "required": 10,
            "selected": len(accepted),
            "selected_candidates": [item["name"] for item in accepted],
            "rejected": rejected,
            "normalizations": normalizations,
            "warning": (
                "No manifest was emitted. Candidate generation must be repaired rather than "
                "weakening the user-functionality or distinctness gates."
            ),
        }
        raise SelectionError(
            f"only {len(accepted)} distinct valid candidates survived; exactly 10 required",
            audit,
        )
    tools = []
    for priority, candidate in enumerate(accepted, start=1):
        tool = {
            key: value for key, value in candidate.items()
            if key not in {"evidence", "return_evidence"}
        }
        tool["priority"] = priority
        proposal_evidence = dict(candidate["evidence"])
        proposal_return_evidence = {
            field: dict(item)
            for field, item in candidate["return_evidence"].items()
        }
        if recon_observations is not None:
            candidate_record = recon_observations["evidence"].get(
                proposal_evidence["evidence_id"]
            )
            if candidate_record:
                proposal_evidence["resolved"] = candidate_record
            for item in proposal_return_evidence.values():
                record = recon_observations["evidence"].get(item["evidence_id"])
                if record:
                    item["resolved"] = record
        tool["proposal_evidence"] = proposal_evidence
        tool["proposal_return_evidence"] = proposal_return_evidence
        tools.append(tool)
    complete_batch = len(tools) >= 10
    manifest = {
        "site_id": proposal["site_id"],
        "site_name": proposal["site_name"],
        "start_url": proposal["start_url"],
        "allowed_hosts": proposal["allowed_hosts"],
        "side_effect_policy": "read_only",
        "tool_count": len(tools),
        "status": (
            "model_selected_unverified"
            if complete_batch
            else "model_selected_partial_unverified"
        ),
        "popularity_method": (
            "Proxy only: visible navigation, prominence, repetition and "
            "core-workflow relevance from bounded reconnaissance."
        ),
        "tools": tools,
        "rejected_candidates": rejected,
        "selection_normalizations": normalizations,
    }
    audit = {
        "status": "pass" if complete_batch else "partial_pass",
        "input_candidates": len(proposal.get("candidates", [])),
        "selected": len(tools),
        "rejected": rejected,
        "normalizations": normalizations,
        "warning": (
            "Selection validates contracts, captured field evidence and pairwise distinctions. "
            "Every selected candidate may proceed immediately to WebWright generation and "
            "live verification; collecting ten candidates first is not required."
        ),
    }
    return manifest, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("proposal", type=Path)
    parser.add_argument("--recon", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    proposal = json.loads(args.proposal.read_text(encoding="utf-8"))
    recon = json.loads(args.recon.read_text(encoding="utf-8"))
    audit_path = args.audit or args.output.with_name("selection_audit.json")
    try:
        manifest, audit = select(proposal, recon)
    except SelectionError as error:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(
            json.dumps(error.audit, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "status": error.audit["status"],
            "selected": error.audit["selected"],
            "rejected": len(error.audit["rejected"]),
            "audit": str(audit_path),
            "error": str(error),
        }, ensure_ascii=False, indent=2))
        raise SystemExit(2) from error
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "selected": len(manifest["tools"]),
        "rejected": len(audit["rejected"]),
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
