import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared_components"))
sys.path.insert(0, str(ROOT / "desktop_version"))

from common.protocol import (MessageReader, pack_message, TYPE_HANDSHAKE_REQ,
    TYPE_HANDSHAKE_ACK, TYPE_RPC_REQUEST, TYPE_RPC_RESULT, TYPE_RELAY_RESULT)
from common.rpc import RPC_CAPABILITY, RelaySession, execute_read
from host.connection import SlaveClient
from host.relay import RelayRouter
from slave.file_service import FileService


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_rpc_concurrent_and_late_reply(self):
        client = SlaveClient("127.0.0.1")
        client._writer = object()
        client.capabilities.add(RPC_CAPABILITY)
        sent = []
        async def send(kind, body):
            sent.append(body)
        client.send = send
        old = asyncio.create_task(client.rpc("file.list", {}, timeout=0.01))
        with self.assertRaises(asyncio.TimeoutError):
            await old
        first = asyncio.create_task(client.rpc("file.list", {"path": "first"}))
        second = asyncio.create_task(client.rpc("file.list", {"path": "second"}))
        await asyncio.sleep(0)
        await client.dispatch_message(TYPE_RPC_RESULT, {"request_id": sent[0]["request_id"], "ok": True})
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        for body in reversed(sent[1:]):
            await client.dispatch_message(TYPE_RPC_RESULT, {
                "request_id": body["request_id"], "ok": True, "result": body["payload"]})
        self.assertEqual((await first)["result"]["path"], "first")
        self.assertEqual((await second)["result"]["path"], "second")
        self.assertEqual(client._rpc_pending, {})

    async def test_disconnect_releases_rpc(self):
        client = SlaveClient("127.0.0.1")
        client._writer = object()
        client.capabilities.add(RPC_CAPABILITY)
        async def send(kind, body):
            pass
        client.send = send
        task = asyncio.create_task(client.rpc("file.stat", {"path": "x"}))
        await asyncio.sleep(0)
        client._fail_pending_rpc()
        with self.assertRaises(ConnectionError):
            await task
        self.assertEqual(client._rpc_pending, {})

    async def test_grants_and_destination_failures(self):
        source = SlaveClient("127.0.0.1", slave_id="source")
        source._writer = object()
        target = SlaveClient("127.0.0.2", slave_id="target")
        clients = {"source": source, "target": target}
        router = RelayRouter(clients)
        replies = []
        async def send(kind, body):
            replies.append(body)
        source.send = send
        body = {"request_id": "test", "target_ip": target.host_ip,
                "operation": "file.list", "payload": {}}
        await router._route(source, body, source._writer)
        self.assertEqual(replies[-1]["error"]["code"], "FORBIDDEN")
        router.grants.add((source.host_ip, target.host_ip, "file.list"))
        await router._route(source, body, source._writer)
        self.assertEqual(replies[-1]["error"]["code"], "OFFLINE")
        target._writer = object()
        await router._route(source, body, source._writer)
        self.assertEqual(replies[-1]["error"]["code"], "UNSUPPORTED")
        clients.pop("target")
        await router._route(source, body, source._writer)
        self.assertEqual(replies[-1]["error"]["code"], "UNKNOWN_TARGET")
        body["operation"] = "exec"
        await router._route(source, body, source._writer)
        self.assertEqual(replies[-1]["error"]["code"], "BAD_REQUEST")

    async def test_tcp_host_routes_slave_to_slave(self):
        """Real CLDC sockets; fixture avoids capture/Qt dependencies."""
        with tempfile.TemporaryDirectory() as directory:
            files = FileService(directory)
            sessions = []
            writers = []
            async def serve(reader, writer):
                writers.append(writer)
                messages = MessageReader(reader)
                async def send(kind, body):
                    writer.write(pack_message(kind, body))
                    await writer.drain()
                session = RelaySession(send)
                sessions.append(session)
                try:
                    kind, body = await messages.read_one()
                    self.assertEqual(kind, TYPE_HANDSHAKE_REQ)
                    await send(TYPE_HANDSHAKE_ACK, {"slave_name": "fixture",
                               "capabilities": [RPC_CAPABILITY]})
                    while True:
                        kind, body = await messages.read_one()
                        if kind == TYPE_RPC_REQUEST:
                            await send(TYPE_RPC_RESULT, execute_read(files, body))
                        elif kind == TYPE_RELAY_RESULT:
                            session.resolve(body)
                except ConnectionError:
                    pass
                finally:
                    session.close()
                    writer.close()
                    await writer.wait_closed()

            server1 = await asyncio.start_server(serve, "127.0.0.1", 0)
            server2 = await asyncio.start_server(serve, "127.0.0.2", 0)
            source = SlaveClient("127.0.0.1", server1.sockets[0].getsockname()[1], "source")
            target = SlaveClient("127.0.0.2", server2.sockets[0].getsockname()[1], "target")
            router = RelayRouter({"source": source, "target": target},
                                 [(source.host_ip, target.host_ip, "file.list"),
                                  (target.host_ip, source.host_ip, "file.list")])
            remove_source = router.attach(source)
            remove_target = router.attach(target)
            loops = []
            try:
                self.assertTrue(await source.connect())
                self.assertTrue(await target.connect())
                source._running = target._running = True
                loops = [asyncio.create_task(c._message_loop()) for c in (source, target)]
                results = await asyncio.gather(
                    sessions[0].request(target.host_ip, "file.list", {"path": directory}),
                    sessions[1].request(source.host_ip, "file.list", {"path": directory}))
                self.assertTrue(all(result["ok"] for result in results))
                self.assertEqual(results[0]["result"]["entries"], [])
                self.assertEqual(len({r["request_id"] for r in results}), 2)
            finally:
                await router.close()
                remove_source()
                remove_target()
                for loop in loops:
                    loop.cancel()
                await asyncio.gather(*loops, return_exceptions=True)
                for client in (source, target):
                    client._running = False
                    if client._writer:
                        client._writer.close()
                        await client._writer.wait_closed()
                server1.close()
                server2.close()
                await server1.wait_closed()
                await server2.wait_closed()
                for writer in writers:
                    writer.close()
                await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
