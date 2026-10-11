import sys
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from cloud_pc.host_program.adapter_client import CodexAdapterClient
from cloud_pc.host_program.codex_adapter_service import AdapterHandler
from cloud_pc.host_program.current_session import CurrentSession


class AdapterTests(unittest.TestCase):
    def test_independent_adapter_http_and_no_idle_collection(self):
        reads, inputs = [], []
        class History:
            def update(self, value):
                return value
        def read():
            reads.append(1)
            return "Codex text"
        backend = SimpleNamespace(current_terminal_text=read, Transcript=History,
            codex_is_busy=lambda: False, paste_to_current_window=inputs.append,
            escape_current_window=lambda: None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), AdapterHandler)
        server.current_session = CurrentSession(backend)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            proxy = CodexAdapterClient(f"http://127.0.0.1:{server.server_port}")
            self.assertTrue(proxy.request("/health")["ok"])
            self.assertEqual(reads, [])
            self.assertEqual(proxy.snapshot()["text"], "Codex text")
            self.assertEqual(reads, [1])
            body = {"request_id": "test-send", "text": "你好"}
            proxy.action("send", body)
            proxy.action("send", body)
            self.assertEqual(inputs, ["你好"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_optional_adapter_unavailable(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), AdapterHandler)
        port = server.server_port
        server.server_close()
        proxy = CodexAdapterClient(f"http://127.0.0.1:{port}")
        self.assertFalse(proxy.snapshot()["available"])
        with self.assertRaises(RuntimeError):
            proxy.action("send", {"request_id": "missing", "text": "1"})


if __name__ == "__main__":
    unittest.main()
