"""Container negative controls; no task solutions or model calls."""
import json
import os
from pathlib import Path
import socket
import sys
from public_recon_probe import main as reconnaissance
import asyncio


def connect(family, address):
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(address)
        return 'connected'
    except OSError as exc:
        return type(exc).__name__


def proxy_attempt(authority, method='CONNECT'):
    with socket.create_connection(('127.0.0.1', 18766), timeout=5) as sock:
        sock.sendall(f'{method} {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n'.encode())
        return sock.recv(200).decode(errors='replace').split('\r\n')[0]


if __name__ == '__main__':
    root = Path(os.environ['WORKSPACE_DIR'])
    results = {
        'host_file_visible': Path('/shared/hans/AGENTS.md').exists(),
        'credentials_visible': Path('/shared/hans/.secrets/website_tool_api_key').exists(),
        'docker_socket': connect(socket.AF_UNIX, '/var/run/docker.sock'),
        'host_unix': connect(socket.AF_UNIX, sys.argv[2]),
        'host_tcp': connect(socket.AF_INET, ('127.0.0.1', int(sys.argv[3]))),
        'external_tcp': connect(socket.AF_INET, ('1.1.1.1', 443)),
        'other_site_proxy': proxy_attempt('example.com:443'),
        'private_proxy': proxy_attempt('127.0.0.1:443'),
        'plain_http_proxy': proxy_attempt('http://arxiv.org/', 'GET'),
        'effective_caps': next(line for line in Path('/proc/self/status').read_text().splitlines()
                               if line.startswith('CapEff:')).split()[-1],
    }
    (root/'security.json').write_text(json.dumps(results, indent=2)+'\n')
    assert not results['host_file_visible'] and not results['credentials_visible']
    assert all(results[key] != 'connected' for key in ('docker_socket', 'host_unix', 'host_tcp', 'external_tcp'))
    assert all('403' in results[key] for key in ('other_site_proxy', 'private_proxy', 'plain_http_proxy'))
    assert int(results['effective_caps'], 16) == 0
    sys.argv = [sys.argv[0], sys.argv[1]]
    asyncio.run(reconnaissance())
