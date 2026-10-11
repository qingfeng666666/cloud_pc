import asyncio
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cloud_pc/shared_components"))
from slave.management_bridge import ManagementBridge
from cloud_pc.management_ui.client import HostClient
from cloud_pc.host_program.android_current import AndroidCurrentChannel


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_http_cldc_host_roundtrip(self):
        bridge = ManagementBridge()
        bridge.start(0)
        called = []
        async def request(slave_id, kind, body, reply, timeout):
            if kind == 0x0606:
                bridge.resolve(body)
                return {"ok": True}
            raise AssertionError("Unexpected network operation")
        def action(kind, body):
            called.append((kind, body))
            return {"ok": True, "number": 1, "message": "submitted"}
        from cloud_pc.host_program.activity import ActivityFeed
        from cloud_pc.host_program.management_session import ManagementSessions
        slave = SimpleNamespace(slave_id="local", host_ip="127.0.0.1", port=9527,
            address="127.0.0.1:9527", state="online", set_management_required=lambda *_: None)
        api = SimpleNamespace(request=request, activity=ActivityFeed(), _clients={"local": slave})
        api.management = ManagementSessions(api)
        await api.management.start("127.0.0.1", 0)
        bridge.host_address = "127.0.0.1"
        bridge.registration_port = api.management.server.sockets[0].getsockname()[1]
        channel = AndroidCurrentChannel(api, slave,
            SimpleNamespace(snapshot=lambda: {"ok": True, "available": True, "text": "reply"}, action=action))
        loop = asyncio.get_running_loop()
        def push(event):
            asyncio.run_coroutine_threadsafe(channel.receive_request(event), loop).result(timeout=1)
        bridge.connected("host", sender=push)
        client = HostClient("http://127.0.0.1:" + str(bridge.server.server_port))
        try:
            for function in [client.current, lambda: client.send("single-send", "测试")]:
                future = asyncio.create_task(asyncio.to_thread(function))
                result = await asyncio.wait_for(future, 2)
                self.assertTrue(result["ok"])
            self.assertEqual(len(called), 1)
            self.assertEqual(called[0][1]["request_id"], "single-send")
        finally:
            await api.management.close()
            await asyncio.to_thread(bridge.close)

    async def test_disconnect_and_timeout_never_retry(self):
        bridge = ManagementBridge()
        self.assertFalse(bridge.request("current", {})["ok"])
        bridge.connected("host", sender=lambda event: None)
        result = await asyncio.to_thread(bridge.request, "send", {}, .01)
        self.assertFalse(result["ok"])
        self.assertEqual(bridge.pending, {})
        future = asyncio.create_task(asyncio.to_thread(bridge.request, "send", {}, 1))
        await asyncio.sleep(.02)
        bridge.disconnected("host")
        self.assertIn("断开", (await future)["error"])

    def test_no_remote_endpoint(self):
        for value in ["http://10.0.0.2:19527", "http://192.168.4.20:19528", "https://example.com:19527"]:
            with self.assertRaises(ValueError):
                HostClient(value)


if __name__ == "__main__":
    unittest.main()
