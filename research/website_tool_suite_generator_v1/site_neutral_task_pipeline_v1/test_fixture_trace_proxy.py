import asyncio
import base64
import io
import json
import threading
import pytest
from runtime.fixture_cdp_trace_proxy import allowed_request,client_frame,read_frame,backend_to_client,capture_before_press
from run_fixture_observer_isolation_20261124 import drain_proxy


def request(line,headers=b''):
    return line+b'\r\nHost: 127.0.0.1\r\n'+headers+b'\r\n'


def test_only_version_and_browser_websocket_handshake_are_allowed():
    assert allowed_request(request(b'GET /json/version/ HTTP/1.1'))
    assert allowed_request(request(b'GET /devtools/browser/01234567-0123-0123-0123-0123456789ab HTTP/1.1',
                                   b'Upgrade: websocket\r\n'))
    assert not allowed_request(request(b'PUT /json/new?http://example.org HTTP/1.1'))
    assert not allowed_request(request(b'GET /json/list HTTP/1.1'))
    assert not allowed_request(request(b'GET /json/version HTTP/1.1',b'Transfer-Encoding: chunked\r\n'))
    assert not allowed_request(request(b'GET /json/version HTTP/1.1',b'Content-Length: 1\r\n'))
    assert not allowed_request(request(b'GET /devtools/page/01234567890123456789012345678901 HTTP/1.1',
                                       b'Upgrade: websocket\r\n'))


def test_injected_websocket_frame_roundtrip():
    async def run():
        value={'id':1000000001,'method':'Page.captureScreenshot','sessionId':'fixture'}
        reader=asyncio.StreamReader();reader.feed_data(client_frame(value));reader.feed_eof()
        first,_,decoded=await read_frame(reader)
        assert first==0x81 and json.loads(decoded)==value
    asyncio.run(run())


def test_injected_response_is_not_sent_to_candidate():
    class Writer:
        def __init__(self):self.data=b''
        def write(self,value):self.data+=value
        async def drain(self):pass
    async def run():
        reader=asyncio.StreamReader();writer=Writer();future=asyncio.get_running_loop().create_future()
        payload=json.dumps({'id':1000000001,'result':{'data':'test'}}).encode()
        reader.feed_data(bytes([0x81,len(payload)])+payload);reader.feed_eof()
        await backend_to_client(reader,writer,{1000000001:future})
        assert future.result()['result']['data']=='test' and writer.data==b''
    asyncio.run(run())


def test_fixture_image_bytes_are_removed_from_trace(tmp_path):
    png=b'\x89PNG\r\n\x1a\nfixture'; encoded=base64.b64encode(png).decode()
    class Process:stdout=io.BytesIO((json.dumps({'event':'action_capture','png':encoded})+'\n').encode())
    events=[];captures=[];ready=threading.Event();done=threading.Event()
    drain_proxy(Process(),tmp_path,events,captures,ready,done)
    assert captures==[png] and ready.is_set() and (tmp_path/'action_capture_00.png').read_bytes()==png
    assert 'png' not in events[0] and encoded not in json.dumps(events)


def test_action_capture_limit_and_missing_session_skip_injection():
    class Writer:
        def __init__(self):self.data=b''
        def write(self,value):self.data+=value
        async def drain(self):pass
    async def run():
        writer=Writer();counter=[3]
        await capture_before_press(writer,{},'session',{},counter)
        assert writer.data==b'' and counter==[3]
        counter=[0]
        await capture_before_press(writer,{},None,{},counter)
        assert writer.data==b'' and counter==[0]
    asyncio.run(run())
