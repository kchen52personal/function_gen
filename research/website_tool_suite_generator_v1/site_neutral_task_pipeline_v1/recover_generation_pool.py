"""Replay saved backend responses deterministically. No model/network requests."""
import argparse
import hashlib
import json
from pathlib import Path
from factory import generate_batched, _response_text, PROJECT


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('configuration')
    args=parser.parse_args()
    config=json.loads(Path(args.configuration).read_text())
    root=PROJECT/'automated_runs'/config['run_name']/'tasks'
    original=json.loads((root/'generation_progress.json').read_text())
    output=root/'recovered_pool_v1'; output.mkdir(exist_ok=False)
    hashes={}
    calls=0
    def replay(prompt,unused,purpose):
        nonlocal calls
        calls+=1
        paths=list((root/f'batch_call_{calls:02d}'/'api_logs/calls').rglob('*.jsonl'))
        if len(paths)!=1:raise ValueError('cannot uniquely identify saved API response')
        path=paths[0]; hashes[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
        rows=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if len(rows)!=1 or not rows[0].get('success'):
            raise ValueError('saved request did not complete successfully')
        return _response_text(rows[0]['response'])
    try:
        generate_batched('',config['website'],config['functionality'],8+config['held_count'],
            config['generation_batch_size'],replay,output,raw_request_argument=config.get('raw_request',False))
    except ValueError as exc:
        if str(exc)!='bounded batched generation did not fill the input pool':raise
    recovered=json.loads((output/'generation_progress.json').read_text())
    verified=recovered['calls']==original['calls'] and recovered['pool_count']==original['pool_count']
    audit={'status':'verified_replay' if verified else 'replay_mismatch','actual_model_calls':0,
           'replayed_saved_responses':calls,'request_count':recovered['pool_count'],
           'source_sha256':hashes,'no_requests_authored_or_edited':True,'held_split_not_opened':True}
    (output/'recovery_audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    print(json.dumps(audit),flush=True)
    if not verified:raise SystemExit(1)


if __name__=='__main__':main()
