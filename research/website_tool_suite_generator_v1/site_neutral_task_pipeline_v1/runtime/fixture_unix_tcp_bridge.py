"""Fixture-only bridge from local TCP to the isolated observer's Unix socket."""
import asyncio


async def copy(reader,writer):
    try:
        while data:=await reader.read(65536):
            writer.write(data);await writer.drain()
    except (OSError,asyncio.CancelledError):
        pass


async def handle(reader,writer):
    remote=None
    try:
        other,remote=await asyncio.open_unix_connection('/bridge/cdp.sock')
        a=asyncio.create_task(copy(reader,remote));b=asyncio.create_task(copy(other,writer))
        _,pending=await asyncio.wait({a,b},return_when=asyncio.FIRST_COMPLETED)
        for task in pending:task.cancel()
        await asyncio.gather(a,b,return_exceptions=True)
    finally:
        writer.close()
        if remote:remote.close()


async def main():
    server=await asyncio.start_server(handle,'127.0.0.1',19223)
    async with server:await server.serve_forever()


if __name__=='__main__':asyncio.run(main())
