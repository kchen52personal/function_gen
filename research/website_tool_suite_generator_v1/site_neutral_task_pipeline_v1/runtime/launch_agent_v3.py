"""Run WebWright in a pre-seeded, isolated task workspace."""
import argparse
from pathlib import Path
from cuawright.webwright.run.cli import run_one


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', required=True)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--start-url', required=True)
    args = parser.parse_args()
    run_one(task=args.task, task_id=args.task_id, start_url=args.start_url,
            config_spec=[str(args.config)], resolved_output_dir=args.workspace)


if __name__ == '__main__':
    main()
