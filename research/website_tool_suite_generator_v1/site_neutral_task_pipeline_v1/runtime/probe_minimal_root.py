"""Proof of concept for hiding host socket paths; not an agent sandbox."""
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading

ROOT = Path('/shared/hans/research/website_tool_suite_generator_v1')
OUT = ROOT/'local_fixtures/ecommerce_storefront_20261117/fixture_minimal_root_probe_20261117_v1.json'
CHILD = r'''
import ctypes, json, os, socket, subprocess, sys
root, allowed, forbidden = sys.argv[1:]
subprocess.run(['/usr/bin/mount','--make-rprivate','/'],check=True,capture_output=True)
subprocess.run(['/usr/bin/mount','-t','tmpfs','-o','size=1m','tmpfs',root],check=True,capture_output=True)
target = root+'/broker.sock'
with open(target,'w'): pass
subprocess.run(['/usr/bin/mount','--bind',allowed,target],check=True,capture_output=True)
os.chroot(root); os.chdir('/')
class Header(ctypes.Structure): _fields_=[('version',ctypes.c_uint32),('pid',ctypes.c_int)]
class Data(ctypes.Structure): _fields_=[('effective',ctypes.c_uint32),('permitted',ctypes.c_uint32),('inheritable',ctypes.c_uint32)]
libc=ctypes.CDLL(None,use_errno=True); header=Header(0x20080522,0); data=(Data*2)()
if libc.capset(ctypes.byref(header),ctypes.byref(data)) != 0: raise OSError(ctypes.get_errno(),'capset')
if libc.prctl(38,1,0,0,0) != 0: raise OSError(ctypes.get_errno(),'no_new_privs')
result={}
for name,path in [('allowed','/broker.sock'),('forbidden',forbidden)]:
  try:
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
      s.settimeout(2); s.connect(path); s.sendall(b'probe'); result[name]='connected'
  except OSError as exc: result[name]=type(exc).__name__
try: os.chroot('/'); result['second_chroot']='allowed'
except OSError as exc: result['second_chroot']=type(exc).__name__
print(json.dumps(result))
'''


def main():
    with tempfile.TemporaryDirectory(prefix='.minroot_',dir=ROOT) as tmp:
        parent=Path(tmp); root=parent/'root'; root.mkdir()
        listeners=[]; seen=[]; threads=[]
        try:
            for name in ('allowed','forbidden'):
                listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
                listener.bind(str(parent/(name+'.sock'))); listener.listen(1); listener.settimeout(5)
                listeners.append(listener)
                def accept_one(server=listener, label=name):
                    try:
                        with server.accept()[0] as conn: seen.append((label,conn.recv(16).decode()))
                    except OSError: pass
                thread=threading.Thread(target=accept_one,daemon=True); thread.start(); threads.append(thread)
            run=subprocess.run(['/usr/bin/unshare','--user','--map-root-user','--mount','--net','--fork',
                '/shared/miniconda3/bin/python','-c',CHILD,str(root),str(parent/'allowed.sock'),str(parent/'forbidden.sock')],
                capture_output=True,text=True,timeout=20,check=False)
            for thread in threads: thread.join(timeout=6)
            try: result=json.loads(run.stdout)
            except ValueError: result={'parse_error':True}
            result.update(returncode=run.returncode,stderr=run.stderr[-1200:],host_accepts=seen)
            result['passed']=(run.returncode==0 and result.get('allowed')=='connected'
                and result.get('forbidden')!='connected' and result.get('second_chroot')=='PermissionError'
                and ('allowed','probe') in seen and not any(x[0]=='forbidden' for x in seen))
        finally:
            for listener in listeners: listener.close()
    with OUT.open('x') as handle: json.dump(result,handle,indent=2); handle.write('\n')
    print(json.dumps(result,indent=2))
    if not result['passed']: raise SystemExit(1)


if __name__=='__main__': main()
