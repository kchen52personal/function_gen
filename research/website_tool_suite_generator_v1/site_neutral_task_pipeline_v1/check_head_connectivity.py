"""Exercise a status-only HEAD probe through the public browser sandbox."""
import argparse
import json
from pathlib import Path
import shlex
from datetime import datetime, timezone

from public_environment import IsolatedPublicEnvironment, workspace


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('website')
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    env=IsolatedPublicEnvironment(args.output,args.website)
    work=workspace()
    command="curl -sS -I -o /dev/null -w 'HTTP status: %{http_code}\\n' --max-time 30 "+shlex.quote(args.website)
    result=env.run_command(work,['-c',command],args.output/'network.jsonl',45)
    record={'utc':datetime.now(timezone.utc).isoformat(),'website':args.website,
            'process_exit':result.returncode,'stdout':result.stdout[:500],
            'stderr':result.stderr[:500],'workspace':str(work),
            'meaning':'Connectivity only; no playback or task correctness verdict.'}
    (args.output/'summary.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record),flush=True)


if __name__=='__main__':main()
