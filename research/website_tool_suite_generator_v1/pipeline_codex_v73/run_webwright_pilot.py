"""Run the actual WebWright loop in an exact, optionally seeded workspace."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from cuawright.webwright.run.cli import run_one


SHARED_ROOT = Path("/shared/hans").resolve()


def inside_shared(path: Path) -> Path:
    resolved = path.resolve()
    if resolved != SHARED_ROOT and SHARED_ROOT not in resolved.parents:
        raise ValueError("WebWright paths must stay inside /shared/hans")
    return resolved


def copy_seed(seed_dir: Path, output_dir: Path) -> None:
    source = inside_shared(seed_dir)
    destination = output_dir / "prior_evidence"
    destination.mkdir(parents=True, exist_ok=True)
    relative_files = (
        Path("plan.md"),
        Path("final_script.py"),
        Path("runtime_error.txt"),
        Path("explore_results_container.py"),
        Path("logs/step_0007.log"),
        Path("final_runs/run_1/screenshot_results_container.png"),
        Path("README.md"),
        Path("prior_evidence.txt"),
        Path("dom_summary.json"),
        Path("aria_snapshot.txt"),
        Path("rendered.html"),
        Path("before.png"),
        Path("after.png"),
    )
    for relative in relative_files:
        source_file = source / relative
        if not source_file.is_file():
            nested_source = source / "prior_evidence" / relative
            if nested_source.is_file():
                source_file = nested_source
        if not source_file.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target)


def embed_seed_text(task: str, output_dir: Path, relative_paths: list[str]) -> str:
    """Embed bounded repair evidence in the initial task message."""

    evidence_root = (output_dir / "prior_evidence").resolve()
    additions: list[str] = []
    total = 0
    for value in relative_paths:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("embedded seed paths must be safe relative paths")
        path = (evidence_root / relative).resolve()
        if path != evidence_root and evidence_root not in path.parents:
            raise ValueError("embedded seed path escapes prior_evidence")
        if not path.is_file():
            raise ValueError(f"embedded seed file does not exist: {relative}")
        text = path.read_text(encoding="utf-8")
        total += len(text)
        if len(text) > 50_000 or total > 100_000:
            raise ValueError("embedded seed text exceeds bounded size")
        additions.append(f"\n\nPRESERVED EVIDENCE: {relative}\n```text\n{text}\n```")
    return task + "".join(additions)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--start-url", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed-dir", type=Path)
    parser.add_argument("--embed-seed-text", action="append", default=[])
    parser.add_argument("--config", action="append", required=True)
    args = parser.parse_args()

    output_dir = inside_shared(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.seed_dir:
        copy_seed(args.seed_dir, output_dir)
    task = embed_seed_text(args.task, output_dir, args.embed_seed_text)

    run_one(
        task=task,
        task_id=args.task_id,
        start_url=args.start_url,
        config_spec=args.config,
        resolved_output_dir=output_dir,
    )


if __name__ == "__main__":
    main()

