"""Short-lived SSH-only VNC login browser. Never sends state to a model."""
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
from uuid import uuid4

from playwright.sync_api import sync_playwright

ROOT=Path('/shared/hans/.secrets/browser_sessions')
URL='https://note.youdao.com/web/'

def main():
    os.umask(0o077)
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    session=ROOT/('desktop_'+uuid4().hex[:12]);session.mkdir(mode=0o700)
    # VNC's classic password protocol only supports the first eight bytes.
    password=secrets.token_hex(4)
    (session/'vnc_password').write_text(password+'\n');(session/'vnc_password').chmod(0o600)
    auth=session/'xauthority'; auth.touch(mode=0o600)
    with socket.socket() as s:
        s.bind(('127.0.0.1',51731));port=s.getsockname()[1]
    log=(session/'desktop.log').open('w')
    xvfb=vnc=None
    state={'website':URL,'status':'starting','port':port,'session':str(session),
           'login_cookie_received':False,'session_unverified':True,
           'security':'host browser; VNC localhost only with password, access via SSH tunnel',
           'duration_seconds':1800}
    def save():
        (session/'metadata.json').write_text(json.dumps(state,indent=2)+'\n')
    save()
    def stop(signum,frame): raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,stop)
    try:
        for _ in range(20):
            display=':'+str(200+secrets.randbelow(700))
            subprocess.run(['xauth','-f',str(auth),'add',display,'MIT-MAGIC-COOKIE-1',secrets.token_hex(16)],
                           check=True,stdout=subprocess.DEVNULL,stderr=log)
            xvfb=subprocess.Popen(['Xvfb',display,'-screen','0','1280x900x24','-auth',str(auth),
                                   '-nolisten','tcp','-noreset'],stdout=log,stderr=log)
            time.sleep(.5)
            if xvfb.poll() is None: break
        else: raise RuntimeError('could not start private display')
        vnc=subprocess.Popen(['x11vnc','-norc','-display',display,'-auth',str(auth),'-localhost',
            '-rfbport',str(port),'-passwdfile',str(session/'vnc_password'),'-forever','-shared',
            '-quiet'],stdout=log,stderr=log)
        time.sleep(.5)
        if vnc.poll() is not None: raise RuntimeError('VNC failed to start')
        os.environ.update(DISPLAY=display,XAUTHORITY=str(auth),
            PLAYWRIGHT_BROWSERS_PATH='/shared/hans/research/website_tool_suite_generator_v1/.playwright-browsers')
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=False,args=['--no-first-run','--no-default-browser-check'])
            context=browser.new_context(viewport={'width':1200,'height':800})
            page=context.new_page()
            try:
                page.goto(URL,wait_until='domcontentloaded',timeout=60000)
            except Exception as exc:
                state['navigation_warning']=type(exc).__name__
            state.update(status='ready',display=display,started_unix=time.time());save()
            print(json.dumps({'status':'ready','session':str(session),'port':port}),flush=True)
            deadline=time.monotonic()+1800
            last_save=0
            while time.monotonic()<deadline and page.is_closed() is False:
                if time.monotonic()-last_save>=5:
                    # These private checkpoints may be pre-login; they are never
                    # considered authenticated without independent validation.
                    temp=session/'storage_state.tmp.json'
                    context.storage_state(path=str(temp));temp.chmod(0o600)
                    temp.replace(session/'storage_state.json')
                    last_save=time.monotonic()
                if (session/'user_finished').exists():
                    context.storage_state(path=str(session/'storage_state.json'))
                    (session/'storage_state.json').chmod(0o600)
                    state['status']='saved_unverified';break
                page.wait_for_timeout(1000)
            else: state['status']='expired_or_browser_closed'
            context.close();browser.close()
    except BaseException as exc:
        state.update(status='stopped',error_type=type(exc).__name__)
        raise
    finally:
        for proc in (vnc,xvfb):
            if proc and proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
        state['finished_unix']=time.time();save();log.close()

if __name__=='__main__':main()
