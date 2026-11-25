"""Fixture-only CDP relay logging command names and bounded mouse coordinates.

Not a security boundary: clients can bypass this listener, open other browsers,
use fragmented/binary messages, or manipulate the page with ordinary methods.
No real-account usage. The relay is for diagnostic experimentation only.
"""
import asyncio
import json
import math
import os
import re
import time
from datetime import datetime,timezone

UPSTREAM=19222
LISTEN=19223
MAX_FRAME=8_000_000
CAPTURE_ID_START=1_000_000_000
CAPTURE_COUNTER=[0]  # Shared across all connections in this single fixture proxy process.


def event(kind,**values):
    print(json.dumps({'time':datetime.now(timezone.utc).isoformat(),'monotonic_ns':time.monotonic_ns(),
                      'event':kind,**values}),flush=True)


def allowed_request(request: bytes) -> bool:
    """Narrow fixture-only handshake; do not proxy arbitrary HTTP endpoints."""
    lines=request.split(b'\r\n')
    if not lines or len(lines[0])>500:return False
    parts=lines[0].split(b' ')
    if len(parts)!=3 or parts[0]!=b'GET' or parts[2]!=b'HTTP/1.1':return False
    path=parts[1]
    if any(line.lower().startswith((b'transfer-encoding:',b'content-length:')) and
           line.lower().replace(b' ',b'')!=b'content-length:0' for line in lines[1:]):
        return False
    upgrade=any(line.lower().startswith(b'upgrade:') and b'websocket' in line.lower() for line in lines[1:])
    if path.rstrip(b'/')==b'/json/version':return not upgrade
    return bool(upgrade and re.fullmatch(rb'/devtools/browser/[a-fA-F0-9-]{32,64}',path))


def client_frame(value):
    """Encode one masked text frame for a host-injected CDP request."""
    payload=json.dumps(value,separators=(',',':')).encode()
    size=len(payload)
    if size<126:header=bytes([0x81,0x80|size])
    elif size<65536:header=bytes([0x81,0x80|126])+size.to_bytes(2,'big')
    else:raise ValueError('injected request too large')
    mask=os.urandom(4)
    return header+mask+bytes(x ^ mask[i%4] for i,x in enumerate(payload))


async def read_frame(reader):
    header=await reader.readexactly(2);first,second=header;size=second&127;extra=b''
    if size==126:extra=await reader.readexactly(2);size=int.from_bytes(extra,'big')
    elif size==127:extra=await reader.readexactly(8);size=int.from_bytes(extra,'big')
    if size>MAX_FRAME:raise ValueError('oversized websocket frame')
    mask=await reader.readexactly(4) if second&128 else b''
    payload=await reader.readexactly(size)
    raw=header+extra+mask+payload
    decoded=bytes(x ^ mask[i%4] for i,x in enumerate(payload)) if mask else payload
    return first,raw,decoded


async def backend_to_client(reader,writer,pending):
    try:
        fragments=[]; payloads=[]; total=0; assembling=False
        while True:
            first,raw,payload=await read_frame(reader);opcode=first&15;final=bool(first&128)
            if opcode==1 and not assembling:
                fragments=[raw];payloads=[payload];total=len(payload);assembling=not final
            elif opcode==0 and assembling:
                fragments.append(raw);payloads.append(payload);total+=len(payload);assembling=not final
            else:
                # Ping/pong and binary messages are forwarded but not used as evidence.
                if opcode in (0,2):event('uninspected_server_frame',opcode=opcode)
                writer.write(raw);await writer.drain();continue
            if total>MAX_FRAME:raise ValueError('oversized websocket message')
            if assembling:continue
            try:item=json.loads(b''.join(payloads))
            except (UnicodeDecodeError,ValueError):item=None
            response_id=item.get('id') if isinstance(item,dict) else None
            if type(response_id) is int and response_id in pending:
                future=pending.pop(response_id)
                if not future.done():future.set_result(item)
            else:
                for frame in fragments:writer.write(frame)
                await writer.drain()
            fragments=[];payloads=[];total=0
    except (asyncio.IncompleteReadError,OSError,ValueError,asyncio.CancelledError) as exc:
        event('server_stream_end',reason=type(exc).__name__)


async def capture_before_press(writer,pending,session_id,details,counter):
    if not isinstance(session_id,str) or not session_id:
        event('action_capture_skipped',reason='missing_session');return
    if counter[0]>=3:
        event('action_capture_skipped',reason='capture_limit');return
    counter[0]+=1; ident=CAPTURE_ID_START+counter[0]
    future=asyncio.get_running_loop().create_future();pending[ident]=future
    writer.write(client_frame({'id':ident,'method':'Page.captureScreenshot',
                               'params':{'format':'png'},'sessionId':session_id}))
    await writer.drain()
    try:
        reply=await asyncio.wait_for(future,timeout=5)
        data=reply.get('result',{}).get('data')
        if not isinstance(data,str) or len(data)>7_000_000:raise ValueError('missing or oversized capture')
        # Private screenshots must never be logged this way; this is a local-only
        # fixture. The host reader strips PNG bytes from the diagnostic trace.
        event('action_capture',png=data,**details)
    except (asyncio.TimeoutError,ValueError):
        event('action_capture_failed')
    finally:
        pending.pop(ident,None)


async def client_to_backend(reader,writer,pending,capture_enabled):
    try:
        while True:
            first,raw,decoded=await read_frame(reader)
            press_details=None
            # Log only full text command frames and a small allowlisted set of
            # mouse-event fields; never record arbitrary params, tokens,
            # cookies, page content or screenshot bytes. Coordinates may still
            # reveal sensitive behavior; this is for local fixture use only.
            if first&128 and first&15==1:
                try:
                    item=json.loads(decoded)
                    if isinstance(item,dict) and type(item.get('id')) is int and item['id']>=CAPTURE_ID_START:
                        event('reserved_candidate_id');return
                    method=item.get('method') if isinstance(item,dict) else None
                    if isinstance(method,str) and len(method)<=100 and all(c.isalnum() or c in '._' for c in method):
                        details={}
                        if method=='Input.dispatchMouseEvent' and isinstance(item.get('params'),dict):
                            params=item['params']; kind=params.get('type'); button=params.get('button')
                            if kind in {'mouseMoved','mousePressed','mouseReleased','mouseWheel'}:details['mouse_type']=kind
                            if button in {'none','left','right','middle','back','forward'}:details['button']=button
                            for axis in ('x','y'):
                                n=params.get(axis)
                                if type(n) in (int,float) and math.isfinite(n) and 0<=n<=10000:details[axis]=round(n,2)
                        event('cdp_method',method=method,**details)
                        if capture_enabled and method=='Input.dispatchMouseEvent' and details.get('mouse_type')=='mousePressed':
                            await capture_before_press(writer,pending,item.get('sessionId'),details,CAPTURE_COUNTER)
                        if method=='Input.dispatchMouseEvent' and details.get('mouse_type')=='mousePressed':
                            press_details=details
                except (UnicodeDecodeError,ValueError):
                    event('unparsed_text_frame')
            elif first&15 in (0,1,2):
                event('uninspected_frame',opcode=first&15)
            writer.write(raw);await writer.drain()
            if press_details is not None:event('mouse_press_forwarded',**press_details)
    except (asyncio.IncompleteReadError,OSError,ValueError,asyncio.CancelledError) as exc:
        event('client_stream_end',reason=type(exc).__name__)


async def handle(client_reader,client_writer):
    server_writer=None
    event('client_connected')
    try:
        request=await asyncio.wait_for(client_reader.readuntil(b'\r\n\r\n'),timeout=5)
        event('request_received',upgrade=b'upgrade: websocket' in request.lower())
        if not allowed_request(request):
            event('request_blocked')
            client_writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            await client_writer.drain()
            return
        # Disable permessage-deflate negotiation: this simple diagnostic relay
        # cannot inspect compressed frames and must not imply otherwise.
        request=b'\r\n'.join(line for line in request.split(b'\r\n')
                             if not line.lower().startswith(b'sec-websocket-extensions:'))
        server_reader,server_writer=await asyncio.open_connection('127.0.0.1',UPSTREAM)
        server_writer.write(request);await server_writer.drain()
        header=await asyncio.wait_for(server_reader.readuntil(b'\r\n\r\n'),timeout=5)
        first_line=header.split(b'\r\n',1)[0].decode('ascii',errors='replace')[:80]
        event('upstream_status',status=first_line)
        if header.startswith(b'HTTP/1.1 101'):
            client_writer.write(header);await client_writer.drain()
            event('websocket_open')
            pending={}
            capture_enabled=os.environ.get('CAPSULE_FIXTURE_CAPTURE_ACTION')=='1'
            a=asyncio.create_task(client_to_backend(client_reader,server_writer,pending,capture_enabled))
            b=asyncio.create_task(backend_to_client(server_reader,client_writer,pending))
            _,pending=await asyncio.wait({a,b},return_when=asyncio.FIRST_COMPLETED)
            for task in pending:task.cancel()
            await asyncio.gather(a,b,return_exceptions=True)
        else:
            lines=header.split(b'\r\n'); length=None
            for line in lines:
                if line.lower().startswith(b'content-length:'):
                    length=int(line.split(b':',1)[1].strip())
            if length is None or length>100_000: raise ValueError('unexpected HTTP response')
            body=await server_reader.readexactly(length)
            body=body.replace(b':19222/',b':19223/')
            lines=[line for line in lines if line and not line.lower().startswith((b'content-length:',b'connection:'))]
            lines.extend([b'Content-Length: '+str(len(body)).encode(),b'Connection: close',b'',b''])
            client_writer.write(b'\r\n'.join(lines)+body);await client_writer.drain()
    except (Exception,asyncio.CancelledError) as exc:
        event('relay_error',reason=type(exc).__name__)
    finally:
        client_writer.close()
        if server_writer:server_writer.close()


async def main():
    socket_path=os.environ.get('CAPSULE_PROXY_UNIX_SOCKET')
    if socket_path:
        if socket_path!='/bridge/cdp.sock':raise ValueError('unexpected bridge path')
        server=await asyncio.start_unix_server(handle,path=socket_path,limit=65536)
        event('listening_unix')
    else:
        server=await asyncio.start_server(handle,'127.0.0.1',LISTEN,limit=65536)
        event('listening',port=LISTEN)
    async with server:await server.serve_forever()


if __name__=='__main__':asyncio.run(main())
