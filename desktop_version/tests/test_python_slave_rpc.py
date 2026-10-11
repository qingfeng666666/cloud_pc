"""Exercise the actual Python SlaveServer when capture dependencies exist."""
import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared_components"))
sys.path.insert(0, str(ROOT / "desktop_version"))

from host.connection import SlaveClient
from host.relay import RelayRouter
from slave.file_service import FileService


@unittest.skipUnless(importlib.util.find_spec("numpy"), "Slave capture requires numpy")
class PythonSlaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_slave_bidirectional_relay(self):
        from slave.connection import SlaveServer
        with tempfile.TemporaryDirectory() as root:
            slaves = [SlaveServer(), SlaveServer()]
            servers = []
            clients = []
            loops = []
            router = None
            try:
                for index, slave in enumerate(slaves, 1):
                    slave.file_service = FileService(root)
                    slave._running = True
                    ip = f"127.0.0.{index}"
                    server = await asyncio.start_server(slave._handle_connection, ip, 0)
                    servers.append(server)
                    client = SlaveClient(ip, server.sockets[0].getsockname()[1], str(index))
                    clients.append(client)
                    self.assertTrue(await client.connect())
                    client._running = True
                    loops.append(asyncio.create_task(client._message_loop()))
                router = RelayRouter({c.slave_id: c for c in clients},
                    [(clients[0].host_ip, clients[1].host_ip, "file.list"),
                     (clients[1].host_ip, clients[0].host_ip, "file.stat")])
                for client in clients:
                    router.attach(client)
                results = await asyncio.gather(
                    next(iter(slaves[0].host_sessions.values())).request(
                        clients[1].host_ip, "file.list", {"path": root}),
                    next(iter(slaves[1].host_sessions.values())).request(
                        clients[0].host_ip, "file.stat", {"path": root}))
                self.assertTrue(all(result["ok"] for result in results))
                self.assertEqual(results[1]["result"]["type"], "dir")
            finally:
                if router:
                    await router.close()
                for task in loops:
                    task.cancel()
                await asyncio.gather(*loops, return_exceptions=True)
                for slave in slaves:
                    slave._running = False
                for client in clients:
                    if client._writer:
                        client._writer.close()
                        await client._writer.wait_closed()
                for server in servers:
                    server.close()
                    await server.wait_closed()
                await asyncio.sleep(0.05)


if __name__ == "__main__":
    unittest.main()
