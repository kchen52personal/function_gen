"""Two fresh command containers must restore the same approved login baseline."""
import argparse
import json
import shutil
from uuid import uuid4
from public_environment import PROJECT, RUNTIME, IsolatedPublicEnvironment, workspace, snapshot


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('website')
    parser.add_argument('--session-directory',required=True)
    parser.add_argument('--session-cookie-name',required=True)
    parser.add_argument('--resource-host',action='append',default=[])
    args=parser.parse_args()
    root=PROJECT/'automated_runs'/('session_runtime_preflight_20261118_'+uuid4().hex[:8])
    root.mkdir()
    env=IsolatedPublicEnvironment(root,args.website,session_directory=args.session_directory,
                                  resource_hosts=args.resource_host)
    records=[]
    for index in (1,2):
        work=workspace();work.chmod(0o700)
        shutil.copyfile(RUNTIME/'session_runtime_probe.py',work/'probe.py')
        result=env.run_command(work,['python3',str(work/'probe.py'),args.website,args.session_cookie_name],
                               root/f'network_{index}.jsonl',180)
        (root/f'stdout_{index}.log').write_text(result.stdout)
        (root/f'stderr_{index}.log').write_text(result.stderr)
        snapshot(work,root/f'command_{index}')
        record={'process_exit':result.returncode}
        if (work/'probe_result.json').is_file():
            record.update(json.loads((work/'probe_result.json').read_text()))
        records.append(record)
        if result.returncode:
            break
    success=len(records)==2 and all(r['process_exit']==0 for r in records)
    summary={'status':'passed' if success else 'failed','checks':records,'model_calls':0,'gpu_used':False,
             'root':str(root),'playback_verified':False}
    (root/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary),flush=True)
    if not success:raise SystemExit(1)


if __name__=='__main__':main()
