#!/usr/bin/env python3
"""Minecraft TCP ingress -> loopback-only SSH reverse tunnel."""
import asyncio
import logging

LISTEN = ("0.0.0.0", 25565)
UPSTREAM = ("127.0.0.1", 25578)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def pipe(src, dst):
    try:
        while chunk := await src.read(65536):
            dst.write(chunk)
            await dst.drain()
    except (ConnectionError, OSError):
        pass


async def handle(client_reader, client_writer):
    try:
        remote_reader, remote_writer = await asyncio.wait_for(
            asyncio.open_connection(*UPSTREAM), timeout=3
        )
    except (ConnectionError, OSError, TimeoutError):
        client_writer.close()
        await client_writer.wait_closed()
        return

    tasks = [
        asyncio.create_task(pipe(client_reader, remote_writer)),
        asyncio.create_task(pipe(remote_reader, client_writer)),
    ]
    try:
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        remote_writer.close()
        client_writer.close()
        await asyncio.gather(
            remote_writer.wait_closed(), client_writer.wait_closed(), return_exceptions=True
        )


async def main():
    server = await asyncio.start_server(handle, *LISTEN)
    logging.info("Gateway %s:%d -> %s:%d", *LISTEN, *UPSTREAM)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

