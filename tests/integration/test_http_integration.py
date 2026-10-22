import asyncio
import unittest

from toffee import ClockCycles, MemoryBackend, Execution


class HttpIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_http_response_does_not_freeze_simulation(self):
        clock = object()
        backend = MemoryBackend(clock)
        request_seen = asyncio.Event()
        release_response = asyncio.Event()

        async def handler(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            request_seen.set()
            await release_response.wait()
            body = str(backend.tick).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        async def request():
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /tick HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
            return int(response.split(b"\r\n\r\n", 1)[1])

        try:
            async with Execution(backend) as execution:
                response_task = asyncio.create_task(request())
                await request_seen.wait()
                before = backend.tick
                await ClockCycles(clock, 5)
                self.assertGreater(backend.tick, before)
                self.assertFalse(response_task.done())

                async with execution.paused():
                    paused_at = backend.tick
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                    self.assertEqual(backend.tick, paused_at)
                    release_response.set()
                    response_tick = await response_task

                self.assertEqual(response_tick, paused_at)
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
