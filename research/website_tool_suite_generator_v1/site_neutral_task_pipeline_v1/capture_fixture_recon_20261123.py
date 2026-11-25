"""Capture one local fixture homepage without agent actions or model calls."""
import asyncio
from uuid import uuid4
from fixture_environment import IsolatedFixtureEnvironment, PROJECT, URL

def main():
    root=PROJECT/'automated_runs'/('verifier_fixture_recon_20261123_'+uuid4().hex[:8]);root.mkdir(mode=0o700)
    with IsolatedFixtureEnvironment(root) as env:
        data=asyncio.run(env.reconnaissance(URL,root/'recon'))
    print('STATUS='+str(data['pages'][0]['http_status']),flush=True)
    print('OUTPUT='+str(root),flush=True)

if __name__=='__main__':main()
