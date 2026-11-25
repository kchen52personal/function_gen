"""Host-owned diagnostic; never supplies a trajectory to the main pipeline."""
import argparse
import json
import shutil
from uuid import uuid4
from public_environment import PROJECT, RUNTIME, IsolatedPublicEnvironment, snapshot, workspace


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('website')
    parser.add_argument('--selector',required=True,help='Input observed in reconnaissance, not a candidate selector')
    parser.add_argument('--resource-host',action='append',default=[])
    args=parser.parse_args()
    root=PROJECT/'automated_runs'/('search_access_diagnostic_20261118_'+uuid4().hex[:8]);root.mkdir()
    work=workspace()
    query='pipeline-access-probe-'+uuid4().hex[:12]
    shutil.copyfile(RUNTIME/'public_search_access_probe.py',work/'probe.py')
    environment=IsolatedPublicEnvironment(root,args.website,resource_hosts=args.resource_host)
    result=environment.run_command(work,['python3',str(work/'probe.py'),args.website,args.selector,query],
                                   root/'network.jsonl',180)
    (root/'stdout.log').write_text(result.stdout);(root/'stderr.log').write_text(result.stderr)
    snapshot(work,root/'browser_artifacts')
    summary={'status':'diagnostic_completed' if result.returncode==0 else 'diagnostic_failed',
             'process_exit':result.returncode,'root':str(root),'query':query,'model_calls':0,'gpu_used':False,
             'eligible_for_development_or_synthesis':False,
             'limitations':['Random non-song query tests whether search is reachable, not song-result or playback correctness.']}
    (root/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
