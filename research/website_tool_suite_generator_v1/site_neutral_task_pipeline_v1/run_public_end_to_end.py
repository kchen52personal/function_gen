"""Bounded public-site run of the same submission-only 8/4 adaptive pipeline."""
import argparse
import json
from pathlib import Path
import signal
from datetime import datetime, timezone
import adaptive
from factory import PROJECT
from public_environment import IsolatedPublicEnvironment
from run_fixture_end_to_end import shadow_review


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('website')
    parser.add_argument('functionality')
    parser.add_argument('--run-name', required=True)
    parser.add_argument('--max-replacement-rounds', type=int, default=3, choices=range(0, 6))
    parser.add_argument('--overall-seconds', type=int, default=21600, choices=range(600, 43201))
    parser.add_argument('--development-count',type=int,default=8,choices=range(2,33))
    parser.add_argument('--held-count',type=int,default=4,choices=range(1,101))
    parser.add_argument('--agent-steps',type=int,default=40,choices=range(1,101))
    parser.add_argument('--agent-calls',type=int,default=40,choices=range(1,111))
    parser.add_argument('--inner-calls',type=int,default=6,choices=range(1,17))
    parser.add_argument('--development-workers',type=int,default=7,choices=range(1,8),
        help='Maximum concurrent independent development tasks (default: 7)')
    parser.add_argument('--allow-media-playback',action='store_true')
    parser.add_argument('--allow-authorized-creation',action='store_true')
    parser.add_argument('--resource-host',action='append',default=[])
    parser.add_argument('--session-directory',help='Host-private user-approved browser-session directory')
    parser.add_argument('--raw-request',action='store_true',help='Function accepts one complete user utterance')
    parser.add_argument('--generation-batch-size',type=int,default=0,choices=range(0,17))
    parser.add_argument('--generation-resume-checkpoint')
    parser.add_argument('--distinct-entities',action='store_true')
    parser.add_argument('--parallel-generation-development',action='store_true')
    parser.add_argument('--diagnostic-head-requests',action='store_true')
    parser.add_argument('--diagnostic-public-get',action='store_true')
    parser.add_argument('--experimental-visual-verification',action='store_true',
        help='Fail-closed development gate and held-out visual assessment; existing development evidence lacks script provenance and will be inconclusive')
    args = parser.parse_args()
    if not args.run_name or '/' in args.run_name or args.run_name in ('.', '..'):
        parser.error('run name must be a simple directory name')
    root = PROJECT/'automated_runs'/args.run_name
    environment = IsolatedPublicEnvironment(root, args.website,agent_steps=args.agent_steps,
        agent_calls=args.agent_calls,inner_calls=args.inner_calls,
        allow_media_playback=args.allow_media_playback,resource_hosts=args.resource_host,
        session_directory=args.session_directory,raw_request_argument=args.raw_request,
        generation_batch_size=args.generation_batch_size,
        generation_resume_checkpoint=args.generation_resume_checkpoint,distinct_entities=args.distinct_entities,
        parallel_generation_development=args.parallel_generation_development,
        diagnostic_head_requests=args.diagnostic_head_requests,diagnostic_public_get=args.diagnostic_public_get,
        allow_authorized_creation=args.allow_authorized_creation,development_workers=args.development_workers)
    drain_development = False
    try:
        state = adaptive.execute(args.website, args.functionality, root,
            max_rounds=args.max_replacement_rounds, target=args.development_count, overall_seconds=args.overall_seconds,
            final_only=True, webwright_only=True, verify_development=False, environment=environment,
            held_count=args.held_count,development_count=args.development_count,
            visual_verification=args.experimental_visual_verification)
        print(json.dumps({'status': state['status'], 'submitted': state.get('submitted_development_count', 0),
                          'held_opened': state['held_opened']}), flush=True)
    except BaseException as exc:
        summary = root/'summary.json'
        if summary.is_file():
            state = json.loads(summary.read_text())
            drain_development = (isinstance(exc, Exception) and not isinstance(exc, TimeoutError)
                and environment._prestarted is not None
                and (root/'tasks/development_freeze.json').is_file()
                and not (root/'tasks/held_out/tasks.json').is_file())
            state.update(status='stopped_interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'stopped_error',
                         error_type=type(exc).__name__, development_worker_draining=drain_development,
                         finished_at=datetime.now(timezone.utc).isoformat())
            adaptive.save(summary, state)
        raise
    finally:
        environment.close(drain_development=drain_development)
        summary = root/'summary.json'
        if summary.is_file() and not json.loads(summary.read_text())['status'].endswith('_running'):
            shadow_review(root, url=args.website)


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise TimeoutError('bounded public experiment received termination signal')
    signal.signal(signal.SIGTERM, interrupted)
    main()
