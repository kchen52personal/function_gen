"""Start an isolated login-only QR browser; output no session credentials."""
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4
from public_environment import IsolatedPublicEnvironment,PROJECT,RUNTIME,workspace

def main():
    os.umask(0o077)
    website='https://note.youdao.com/web/'
    private=Path('/shared/hans/.secrets/browser_sessions')
    private.mkdir(mode=0o700,parents=True,exist_ok=True)
    session=private/('session_'+uuid4().hex[:12]);session.mkdir(mode=0o700)
    work=workspace();work.chmod(0o700)
    shutil.copyfile(RUNTIME/'private_qr_browser.py',work/'private_qr_browser.py')
    meta={'website':website,'purpose':'login_only','login_cookie_received':False,
          'session_unverified':True,'workspace':str(work)}
    (session/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
    print(json.dumps({'session_directory':str(session),'workspace':str(work),
                      'status':str(work/'status.json'),'image':str(work/'login.png')}),flush=True)
    env=IsolatedPublicEnvironment(session,website,resource_hosts=(
        'shared-https.ydstatic.com','urswebzj.nosdn.127.net','passport.youdao.com','mp.weixin.qq.com'))
    result=env.run_command(work,['python3',str(work/'private_qr_browser.py'),website,'500','563'],
                           session/'network.jsonl',720,login_bootstrap=True)
    (session/'stdout.log').write_text(result.stdout);(session/'stderr.log').write_text(result.stderr)
    state=work/'private_storage_state.json'
    if state.is_file() and not state.is_symlink():
        shutil.copyfile(state,session/'storage_state.json');(session/'storage_state.json').chmod(0o600)
        meta['state_saved_unverified']=True
    meta['process_exit']=result.returncode
    (session/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
    print(json.dumps({'process_exit':result.returncode,'session_saved_unverified':bool(meta.get('state_saved_unverified'))}),flush=True)

if __name__=='__main__':main()
