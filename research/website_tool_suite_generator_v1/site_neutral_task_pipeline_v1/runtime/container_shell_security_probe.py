"""Negative controls executed inside the disposable agent container."""
import json
from pathlib import Path
import socket
import sys


def attempt(family,address):
    try:
        with socket.socket(family,socket.SOCK_STREAM) as conn:
            conn.settimeout(2); conn.connect(address)
            return 'connected'
    except OSError as exc: return type(exc).__name__


def main():
    out={'host_unix':attempt(socket.AF_UNIX,sys.argv[1]),
         'docker_socket':attempt(socket.AF_UNIX,'/var/run/docker.sock'),
         'host_tcp':attempt(socket.AF_INET,('127.0.0.1',int(sys.argv[2]))),
         'external_tcp':attempt(socket.AF_INET,('1.1.1.1',443)),
         'host_file_visible':Path('/shared/hans/AGENTS.md').exists(),
         'fixture_source_visible':Path('/fixture/serve_fixture.py').exists(),
         'effective_caps':next(x for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('CapEff:')).split()[-1]}
    try:
        Path('/root_write_probe').write_text('bad'); out['root_write']='allowed'
    except OSError as exc: out['root_write']=type(exc).__name__
    print(json.dumps(out))


if __name__=='__main__': main()
