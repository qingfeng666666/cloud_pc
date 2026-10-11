import json
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "desktop_version"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared_components"))
from cloud_pc.host_program.current_session import CurrentSession
from cloud_pc.management_ui.client import HostClient


class History:
    def update(self, value):
        return value


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.typed = []
        self.escapes = []
        self.now = [0]
        self.busy = False
        self.backend = SimpleNamespace(
            current_terminal_text=lambda: "对话文字",
            Transcript=History,
            codex_is_busy=lambda: self.busy,
            paste_to_current_window=self.typed.append,
            escape_current_window=lambda: self.escapes.append("ESC"))
        self.service = CurrentSession(self.backend, clock=lambda: self.now[0])

    def test_single_send_and_dedup(self):
        body = {"request_id": "desktop-1", "text": "你好"}
        first = self.service.action("send", body)
        second = self.service.action("send", body)
        self.assertEqual(first, second)
        self.assertTrue(first["ok"])
        self.assertEqual(self.typed, ["你好"])
        self.assertEqual(first["number"], 1)
        with self.assertRaises(ValueError):
            self.service.action("send", {**body, "text": "不同"})

    def test_concurrent_duplicate_send(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.service.action("send", {
                "request_id": "android-1", "text": "1"}), range(4)))
        self.assertTrue(all(r["ok"] for r in results))
        self.assertEqual(self.typed, ["1"])

    def test_stop_idle_busy_duplicate_and_new_work(self):
        self.assertFalse(self.service.action("stop", {"request_id": "idle"})["ok"])
        self.busy = True
        self.assertTrue(self.service.action("stop", {"request_id": "desktop-stop"})["ok"])
        self.assertFalse(self.service.action("stop", {"request_id": "android-stop"})["ok"])
        self.assertEqual(self.escapes, ["ESC"])
        self.busy = False
        self.service.snapshot()
        self.now[0] = 3
        self.service.snapshot()
        self.busy = True
        self.assertTrue(self.service.action("stop", {"request_id": "next-work"})["ok"])
        self.assertEqual(len(self.escapes), 2)

    def test_invalid_send_and_failed_send_not_retried(self):
        for text in ["", "a\nb", "中" * 400]:
            with self.assertRaises(ValueError):
                self.service.action("send", {"request_id": "invalid", "text": text})
        calls = []
        def failed(value):
            calls.append(value)
            raise RuntimeError("Desktop locked")
        self.backend.paste_to_current_window = failed
        body = {"request_id": "fail", "text": "1"}
        self.assertFalse(self.service.action("send", body)["ok"])
        self.service.action("send", body)
        self.assertEqual(calls, ["1"])

    def test_snapshot_revision_and_stale(self):
        first = self.service.snapshot()
        self.assertEqual(first["revision"], self.service.snapshot()["revision"])
        self.service.action("send", {"request_id": "send", "text": "1"})
        self.assertNotIn("fingerprint", self.service.snapshot()["latest"])
        def unavailable():
            raise RuntimeError("Desktop locked")
        self.backend.current_terminal_text = unavailable
        stale = self.service.snapshot()
        self.assertFalse(stale["available"])
        self.assertEqual(stale["text"], "对话文字")

    def test_real_http_api_and_client(self):
        from host.api_server import HostAPI
        loop = asyncio.new_event_loop()
        ready = threading.Event()
        servers = []
        def serve():
            asyncio.set_event_loop(loop)
            server = loop.run_until_complete(asyncio.start_server(HostAPI()._handle_http, "127.0.0.1", 0))
            servers.append(server)
            ready.set()
            loop.run_forever()
            server.close()
            loop.run_until_complete(server.wait_closed())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()
        with patch("host.api_server.CodexAdapterClient", return_value=self.service):
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(timeout=3))
            try:
                client = HostClient(f"http://127.0.0.1:{servers[0].sockets[0].getsockname()[1]}")
                self.assertTrue(client.current()["available"])
                self.assertTrue(client.send("http-send", "你好")["ok"])
                client.send("http-send", "你好")
                self.assertEqual(self.typed, ["你好"])
                self.assertFalse(client.stop("http-stop")["ok"])
            finally:
                loop.call_soon_threadsafe(loop.stop)
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
