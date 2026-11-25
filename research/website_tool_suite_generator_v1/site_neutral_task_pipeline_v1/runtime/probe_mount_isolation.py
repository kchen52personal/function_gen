"""Check mount-isolation support without running agent code or models."""
from pathlib import Path
import json
import subprocess
import tempfile

ROOT = Path('/shared/hans/research/website_tool_suite_generator_v1')
OUT = ROOT/'local_fixtures/ecommerce_storefront_20261117/fixture_mount_probe_20261117_v1.json'
CHILD = '''
import json, subprocess, sys
mount = subprocess.run(['/usr/bin/mount','-t','tmpfs','-o','size=1m','tmpfs',sys.argv[1]],capture_output=True,text=True)
result = {'mount_returncode':mount.returncode, 'mount_stderr':mount.stderr[:500]}
if mount.returncode == 0:
    unmount = subprocess.run(['/usr/bin/umount',sys.argv[1]],capture_output=True,text=True)
    result['unmount_returncode'] = unmount.returncode
print(json.dumps(result))
'''


def main():
    with tempfile.TemporaryDirectory(prefix='.mount_check_', dir=ROOT) as directory:
        run = subprocess.run(['/usr/bin/unshare','--user','--map-root-user','--mount','--fork',
            '/shared/miniconda3/bin/python','-c',CHILD,directory],
            text=True,capture_output=True,timeout=20,check=False)
        try:
            result=json.loads(run.stdout)
        except ValueError:
            result={'parse_error':True}
        result.update(returncode=run.returncode,stderr=run.stderr[:500])
        result['passed']=(run.returncode==0 and result.get('mount_returncode')==0 and result.get('unmount_returncode')==0)
    with OUT.open('x') as handle:
        json.dump(result,handle,indent=2); handle.write('\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
