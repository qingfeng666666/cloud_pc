"""Host-only tests: no Qt installation or remote devices required."""
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared_components"))
sys.path.insert(0, str(ROOT / "desktop_version"))

from host.api_server import HostAPI
from host.connection import SlaveClient
from host.main import build_arg_parser


class HostTests(unittest.IsolatedAsyncioTestCase):
    def test_headless_parser(self):
        self.assertTrue(build_arg_parser().parse_args(["--headless"]).headless)

    async def test_subscribers_preserve_ui(self):
        client = SlaveClient("127.0.0.1")
        seen = []

        @client.on_message(0x0181)
        async def ui(payload):
            seen.append("ui")

        async def observer(payload):
            seen.append("api")

        remove = client.subscribe(0x0181, observer)
        await client.dispatch_message(0x0181, {})
        remove()
        await client.dispatch_message(0x0181, {})
        self.assertEqual(seen, ["api", "ui", "ui"])
        self.assertEqual(client._listeners, {})

    async def test_concurrent_requests_serialized(self):
        client = SlaveClient("127.0.0.1")
        client._writer = object()
        api = HostAPI()
        api.register_client(client)
        sends = []

        async def send(kind, payload):
            sends.append(payload["path"])
            async def respond():
                await asyncio.sleep(0.01)
                await client.dispatch_message(0x0181, payload)
            asyncio.create_task(respond())

        client.send = send
        results = await asyncio.gather(*(
            api.request(client.slave_id, 0x0101, {"path": path}, 0x0181)
            for path in ["one", "two"]
        ))
        self.assertEqual([r["path"] for r in results], ["one", "two"])
        self.assertEqual(sends, ["one", "two"])
        api.unregister_client(client.slave_id)
        self.assertEqual(client._listeners, {})

    async def test_timeout_cleanup(self):
        client = SlaveClient("127.0.0.1")
        client._writer = object()
        async def send(kind, payload):
            pass
        client.send = send
        api = HostAPI()
        api.register_client(client)
        result = await api.request(client.slave_id, 0x0101, {}, 0x0181, timeout=0.01)
        self.assertIn("error", result)
        api.unregister_client(client.slave_id)
        self.assertEqual(client._listeners, {})


if __name__ == "__main__":
    unittest.main()
