import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "cloud_pc" / "shared_components"))
sys.path.insert(0, str(ROOT / "cloud_pc" / "desktop_version"))
from host.connection import SlaveClient
from host.api_server import HostAPI


class ManualConnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_client_restarts_without_duplicate_task(self):
        client = SlaveClient("127.0.0.1", 9527)
        client.state = "failed"
        started = asyncio.Event()
        finish = asyncio.Event()
        async def connect():
            started.set()
            await finish.wait()
            return False
        async def exhausted():
            client._running = False
            client.state = "failed"
        client.connect = connect
        client._reconnect_loop = exhausted
        self.assertTrue((await client.manual_connect())["started"])
        await started.wait()
        self.assertFalse((await client.manual_connect())["started"])
        finish.set()
        await client._run_task
        self.assertEqual(client.state, "failed")
        self.assertTrue((await client.manual_connect())["started"])
        await client._run_task

    async def test_online_client_unchanged(self):
        client = SlaveClient("127.0.0.1")
        client.state = "online"
        client._writer = object()
        self.assertFalse((await client.manual_connect())["started"])
        self.assertIsNone(client._run_task)

    async def test_route_only_configured_post(self):
        api = HostAPI()
        client = SlaveClient("127.0.0.1", slave_id="test")
        client.manual_connect = AsyncMock(return_value={"ok": True, "started": True})
        api._clients["test"] = client
        self.assertFalse((await api._route("POST", "missing", "connect", {}, "{}"))["ok"])
        self.assertFalse((await api._route("GET", "test", "connect", {}, ""))["ok"])
        self.assertTrue((await api._route("POST", "test", "connect", {}, "{}"))["ok"])
        client.manual_connect.assert_awaited_once()
