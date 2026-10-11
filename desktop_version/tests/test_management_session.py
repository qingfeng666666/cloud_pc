import asyncio
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cloud_pc/shared_components"))
sys.path.insert(0, str(ROOT / "cloud_pc/desktop_version"))
from host.connection import SlaveClient
from host.api_server import HostAPI
from cloud_pc.host_program.management_session import ManagementSessions
from slave.management_bridge import ManagementBridge


class ManagementTests(unittest.IsolatedAsyncioTestCase):
    def api(self):
        api = HostAPI()
        for number in (1, 2):
            c = SlaveClient("127.0.0.1", 9526 + number, slave_id=str(number))
            c.manual_connect = AsyncMock(return_value={"ok": True, "started": False})
            api._clients[c.slave_id] = c
        return api

    async def test_takeover_retires_old_ui_and_only_one_persistent_slave(self):
        api = self.api()
        a, b = api._clients.values()
        sessions = api.management
        self.assertTrue((await sessions.activate("a" * 32, a.address))["ok"])
        self.assertTrue(a.management_required)
        self.assertFalse(b.management_required)
        self.assertTrue((await sessions.activate("b" * 32, b.address))["ok"])
        self.assertFalse(a.management_required)
        self.assertTrue(b.management_required)
        self.assertEqual(sessions.authorize("a" * 32, a.address)["code"], "management_inactive")
        self.assertFalse((await sessions.activate("a" * 32, a.address))["ok"])
        self.assertFalse(sessions.release("a" * 32, a.address)["ok"])
        self.assertTrue(sessions.release("b" * 32, b.address)["ok"])
        self.assertFalse(any(c.management_required for c in api._clients.values()))

    async def test_watch_restores_exhausted_owner_only(self):
        api = self.api()
        a, b = api._clients.values()
        api.management.interval = .01
        await api.management.activate("a" * 32, a.address)
        a.manual_connect.reset_mock()
        await api.management.start()
        try:
            await asyncio.sleep(.035)
            self.assertGreaterEqual(a.manual_connect.await_count, 2)
            b.manual_connect.assert_not_awaited()
            self.assertNotIn("session_id", api.management.status())
        finally:
            await api.close()

    async def test_restore_owner_by_address_after_host_restart(self):
        api = self.api()
        a = api._clients["1"]
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "management.json"
            sessions = ManagementSessions(api, state_path=state)
            await sessions.activate("a" * 32, a.address)
            restored = ManagementSessions(api, state_path=state)
            self.assertIs(restored.client(), a)
            self.assertIsNone(restored.authorize("a" * 32, a.address))

    async def test_slave_bootstrap_then_host_reconnect_and_release(self):
        api = self.api()
        await api.management.start("127.0.0.1", 0)
        bridge = ManagementBridge(9527)
        bridge.host_address = "127.0.0.1"
        bridge.registration_port = api.management.server.sockets[0].getsockname()[1]
        try:
            result = await asyncio.to_thread(bridge.register, "activate", {"session_id": "a" * 32})
            self.assertTrue(result["ok"])
            self.assertTrue(api._clients["1"].management_required)
            result = await asyncio.to_thread(bridge.register, "exec", {"session_id": "a" * 32})
            self.assertFalse(result["ok"])
            result = await asyncio.to_thread(bridge.register, "release", {"session_id": "a" * 32})
            self.assertTrue(result["ok"])
        finally:
            await api.close()

    async def test_persistent_reconnect_exceeds_normal_limit_then_stops(self):
        c = SlaveClient("127.0.0.1")
        c._running = True
        c.set_management_required(True, .001)
        c.connect = AsyncMock(return_value=False)
        with patch("host.connection.RECONNECT_MAX", 2):
            task = asyncio.create_task(c._reconnect_loop())
            await asyncio.sleep(.02)
            self.assertGreater(c.connect.await_count, 2)
            c.set_management_required(False)
            await asyncio.wait_for(task, .5)
        self.assertEqual(c.state, "failed")

    async def test_local_ui_takeover_denies_old_current_read(self):
        from cloud_pc.management_ui.client import HostClient
        api = self.api()
        api.adapter = SimpleNamespace(snapshot=lambda: {"ok": True, "text": "hello"})
        server = await asyncio.start_server(api._handle_http, "127.0.0.1", 0)
        url = "http://127.0.0.1:" + str(server.sockets[0].getsockname()[1])
        first, second = HostClient(url), HostClient(url)
        try:
            self.assertTrue((await asyncio.to_thread(first.current))["ok"])
            self.assertTrue((await asyncio.to_thread(second.current))["ok"])
            self.assertEqual((await asyncio.to_thread(first.current))["code"], "management_inactive")
            self.assertFalse((await asyncio.to_thread(first.activate))["ok"])
            self.assertTrue((await asyncio.to_thread(first.activate, True))["ok"])
        finally:
            server.close()
            await server.wait_closed()

    async def test_unconfigured_bootstrap_peer_and_port_rejected(self):
        api = self.api()
        await api.management.start("127.0.0.1", 0)
        bridge = ManagementBridge(9999)
        bridge.host_address = "127.0.0.1"
        bridge.registration_port = api.management.server.sockets[0].getsockname()[1]
        try:
            result = await asyncio.to_thread(bridge.register, "activate", {"session_id": "a" * 32})
            self.assertFalse(result["ok"])
            for client in api._clients.values(): client.host_ip = "127.0.0.2"
            result = await asyncio.to_thread(bridge.register, "activate", {"session_id": "a" * 32})
            self.assertFalse(result["ok"])
            self.assertIsNone(api.management.owner)
        finally:
            await api.close()

    async def test_unregistered_remote_ui_cannot_send(self):
        from cloud_pc.host_program.android_current import AndroidCurrentChannel
        api = self.api()
        api.request = AsyncMock(return_value={"ok": True})
        adapter = SimpleNamespace(action=lambda *_: self.fail("Inactive UI reached adapter"))
        await AndroidCurrentChannel(api, api._clients["1"], adapter).management({
            "send_id": "a" * 32, "operation": "send", "payload": {"text": "test"}})
        self.assertEqual(api.request.call_args.args[2]["result"]["code"], "management_inactive")
