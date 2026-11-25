"""Container-local loopback proxy -> bounded host-owned Unix egress relay."""
import os
from pathlib import Path
import select
import socket
import socketserver
import subprocess
import sys
import threading
import time


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as upstream:
            upstream.settimeout(20)
            upstream.connect(str(Path(os.environ['WORKSPACE_DIR'])/'.egress.sock'))
            until = time.monotonic()+250
            while time.monotonic() < until:
                ready, _, _ = select.select([self.request, upstream], [], [], 10)
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is self.request else self.request).sendall(data)


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True


def main():
    # sitecustomize configures Playwright, without selectors or task answers.
    policy = Path('/tmp/public_policy')
    policy.mkdir()
    (policy/'sitecustomize.py').write_text(Path('/tmp/public_browser_policy.py').read_text())
    server = Server(('127.0.0.1', 18766), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ, PYTHONPATH=str(policy)+':'+os.environ['PYTHONPATH'],
               HTTPS_PROXY='http://127.0.0.1:18766', HTTP_PROXY='http://127.0.0.1:18766')
    try:
        return subprocess.run(sys.argv[1:], env=env, check=False).returncode
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    raise SystemExit(main())
