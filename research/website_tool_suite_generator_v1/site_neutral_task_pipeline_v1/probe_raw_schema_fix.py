"""One-call live schema probe; never feeds examples into an experiment."""
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

import factory


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    output = Path(sys.argv[2])
    output.mkdir(parents=True, exist_ok=False)
    calls = []

    def once(prompt, path, purpose):
        if calls:
            raise RuntimeError('probe API budget exhausted; no retry sent')
        calls.append(True)
        return factory.model_request(prompt, path, purpose)

    recon = {'start_url': config['website'], 'allowed_host': urlparse(config['website']).hostname, 'pages': []}
    try:
        _, summary = factory.create_taskset(
            config['website'], config['functionality'], recon, output / 'probe',
            requester=once, development_count=2, held_count=2,
            raw_request_argument=True, generation_batch_size=4,
            distinct_entities=True, allow_media_playback=True)
        result = {'status': 'accepted', 'pool_count': summary['batch_generation']['pool_count']}
    except Exception as exc:
        progress_path = output / 'probe' / 'generation_progress.json'
        progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}
        result = {'status': 'not_accepted', 'error_type': type(exc).__name__,
                  'pool_count': progress.get('pool_count'),
                  'first_call': (progress.get('calls') or [None])[0]}
    result.update(api_calls_sent=len(calls), used_for_evaluation=False,
                  note='Isolated format probe; no browser and no benchmark score.')
    (output / 'probe_result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
