"""Host-owned HTTPS CONNECT relay for a network-disabled browser container.

Only the host-selected site's hostname/subdomains and globally routed resolved
addresses on port 443 are allowed. TLS is not intercepted: this is an egress
boundary, NOT enforcement that the website operation is read-only.
"""
import ipaddress
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import select
import socket
import socketserver
import threading
import time
from uuid import uuid4


def error_reason(exc, phase):
    """Fixed, non-sensitive labels; never persist arbitrary exception text."""
    if isinstance(exc, ValueError):
        known = {
            'workspace connection budget exhausted': 'connection_budget_exhausted',
            'tunnel byte limit exceeded': 'tunnel_byte_limit_exceeded',
            'destination outside host-selected site': 'destination_not_allowed',
            'DNS contains a non-public address': 'dns_non_public_address',
            'only DNS-host HTTPS tunnels on port 443 are allowed': 'invalid_destination',
            'literal IP destinations are not allowed': 'literal_ip_not_allowed',
            'oversized request line': 'oversized_request',
            'incomplete or oversized headers': 'invalid_headers',
            'only HTTPS CONNECT is supported': 'invalid_method',
        }
        return known.get(str(exc), 'invalid_request_or_relay_value')
    if isinstance(exc, socket.timeout):
        return 'timeout'
    if isinstance(exc, OSError):
        return 'network_os_error'
    return 'unexpected_error'


def target_host(authority, allowed_host, resource_hosts=()):
    if not re.fullmatch(r'[A-Za-z0-9.-]+:443', authority):
        raise ValueError('only DNS-host HTTPS tunnels on port 443 are allowed')
    host = authority[:-4].lower()
    if not any(host == domain or host.endswith('.' + domain) for domain in (allowed_host, *resource_hosts)):
        raise ValueError('destination outside host-selected site')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise ValueError('literal IP destinations are not allowed')


def public_addresses(host):
    rows = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not rows or any(not ipaddress.ip_address(row[4][0]).is_global for row in rows):
        raise ValueError('DNS contains a non-public address')
    return rows


def relay(left, right, seconds=240, byte_limit=16_000_000):
    until = time.monotonic() + seconds
    transferred = 0
    while time.monotonic() < until:
        readable, _, _ = select.select([left, right], [], [], min(10, max(0, until-time.monotonic())))
        for source in readable:
            data = source.recv(65536)
            if not data:
                return {'reason': 'peer_closed', 'bytes_transferred': transferred}
            transferred += len(data)
            if transferred > byte_limit:
                raise ValueError('tunnel byte limit exceeded')
            (right if source is left else left).sendall(data)
    return {'reason': 'duration_limit', 'bytes_transferred': transferred}


class PublicEgress:
    def __init__(self, workspace, allowed_host, log, *, resource_hosts=()):
        self.path = Path(workspace) / '.egress.sock'
        if len(str(self.path).encode()) >= 108:
            raise ValueError('workspace path too long for egress socket')
        if len(resource_hosts)>4 or not all(re.fullmatch(r'[a-z0-9.-]+', h) for h in (allowed_host,*resource_hosts)):
            raise ValueError('invalid allowed host')
        # Reuse the target check to reject IP literals, including publicly routed IPs.
        for h in (allowed_host,*resource_hosts):target_host(h+':443',h)
        self.resource_hosts=tuple(resource_hosts)
        self.allowed_host, self.log = allowed_host, Path(log)
        self.count, self.lock = 0, threading.Lock()
        self.slots = threading.BoundedSemaphore(24)
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                connection_id = uuid4().hex
                started=time.monotonic()
                started_utc=datetime.now(timezone.utc).isoformat()
                if not owner.slots.acquire(blocking=False):
                    owner.record('', 'denied_or_failed', 'ConcurrencyLimit',
                                 elapsed_seconds=time.monotonic()-started, connection_id=connection_id,
                                 started_utc=started_utc, phase='admission', reason='concurrency_limit')
                    return
                authority = ''
                phase = 'parse'
                budget_count = None
                try:
                    self.connection.settimeout(20)
                    first = self.rfile.readline(4097)
                    if len(first) > 4096:
                        raise ValueError('oversized request line')
                    method, authority, version = first.decode('ascii').strip().split()
                    if method != 'CONNECT' or version not in ('HTTP/1.0', 'HTTP/1.1'):
                        raise ValueError('only HTTPS CONNECT is supported')
                    size = 0
                    while True:
                        line = self.rfile.readline(4097)
                        size += len(line)
                        if not line or size > 16384:
                            raise ValueError('incomplete or oversized headers')
                        if line == b'\r\n':
                            break
                    owner.record(authority, 'started', elapsed_seconds=time.monotonic()-started,
                                 connection_id=connection_id, started_utc=started_utc,
                                 phase='destination_validation')
                    phase = 'destination_validation'
                    host = target_host(authority, owner.allowed_host, owner.resource_hosts)
                    phase = 'connection_budget'
                    with owner.lock:
                        owner.count += 1
                        budget_count = owner.count
                        if owner.count > 512:
                            raise ValueError('workspace connection budget exhausted')
                    remote = None
                    phase = 'dns_and_connect'
                    for family, kind, proto, _, address in public_addresses(host):
                        candidate = socket.socket(family, kind, proto)
                        candidate.settimeout(15)
                        try:
                            candidate.connect(address)  # Numeric address: no second DNS lookup.
                            remote = candidate
                            break
                        except OSError:
                            candidate.close()
                    if remote is None:
                        raise OSError('no public upstream reachable')
                    with remote:
                        owner.record(authority, 'allowed',elapsed_seconds=time.monotonic()-started,
                                     connection_id=connection_id, started_utc=started_utc,
                                     phase='connected', budget_count=budget_count)
                        self.connection.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                        phase = 'tunnel_relay'
                        outcome = relay(self.connection, remote)
                        owner.record(authority, 'closed', elapsed_seconds=time.monotonic()-started,
                                     connection_id=connection_id, started_utc=started_utc,
                                     phase='tunnel_relay', budget_count=budget_count,
                                     reason=outcome['reason'], bytes_transferred=outcome['bytes_transferred'])
                except Exception as exc:
                    owner.record(authority, 'denied_or_failed', type(exc).__name__,
                                 elapsed_seconds=time.monotonic()-started, connection_id=connection_id,
                                 started_utc=started_utc, phase=phase, budget_count=budget_count,
                                 reason=error_reason(exc, phase))
                    try:
                        self.connection.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
                    except OSError:
                        pass
                finally:
                    owner.slots.release()

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True
        self.server = Server(str(self.path), Handler)
        self.path.chmod(0o600)
        self.identity = self.path.stat().st_ino
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def record(self, authority, status, error=None, elapsed_seconds=None, *, connection_id=None,
               started_utc=None, phase=None, budget_count=None, reason=None, bytes_transferred=None):
        # Log only a syntactically valid host:port, never a raw URL/header.
        safe_authority = authority if len(authority) <= 255 and re.fullmatch(r'[A-Za-z0-9.-]+:[0-9]+', authority) else '<invalid>'
        with self.lock:
            with self.log.open('a') as stream:
                stream.write(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),
                    'authority': safe_authority, 'status': status, 'error_type': error,
                    'connection_id': connection_id, 'started_utc': started_utc,
                    'phase': phase, 'reason': reason, 'budget_count': budget_count,
                    'budget_limit': 512, 'bytes_transferred': bytes_transferred,
                    'elapsed_seconds':round(elapsed_seconds,3) if elapsed_seconds is not None else None})+'\n')

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.path.exists() and self.path.stat().st_ino == self.identity:
            self.path.unlink()
