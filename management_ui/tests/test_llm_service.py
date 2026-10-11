import asyncio
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cloud_pc/shared_components"))
sys.path.insert(0, str(ROOT / "cloud_pc/desktop_version"))
from cloud_pc.host_program.llm_service import LLMService
from cloud_pc.host_program.llm_backends import LocalHTTP, DshBackend, OpenCodeBackend, snapshot
from cloud_pc.host_program.android_current import AndroidCurrentChannel
from cloud_pc.management_ui.client import HostClient
from host.api_server import HostAPI
from slave.management_bridge import ROUTES


class FakeBackend:
    def __init__(self):
        self.calls, self.busy = [], False
        self.text = "历史内容"
    def sessions(self, cursor=None): return [{"id": "session-1", "title": "测试会话"}], None
    def create(self): self.calls.append("create"); return "new-session"
    def open(self, sid): self.calls.append(("open", sid)); return self.current(sid)
    def current(self, sid): return snapshot([{"role": "assistant", "text": self.text}], self.busy)
    def send(self, sid, text): self.calls.append(("send", sid, text)); self.busy = True
    def stop(self, sid): self.calls.append(("stop", sid))
    def close(self): pass


class LLMTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.service = LLMService(backends={"codex": self.backend}, state_path=":memory:")
    def tearDown(self): self.service.close()
    def call(self, op, **data):
        return self.service.dispatch(op, {"provider": "codex", "session_id": "session-1", **data})
    def test_working_timer_per_session(self):
        with patch('cloud_pc.host_program.llm_service.time.monotonic', return_value=100):
            self.call('send', request_id='timer-send', text='计时')
        with patch('cloud_pc.host_program.llm_service.time.monotonic', return_value=123):
            self.assertEqual(self.call('current')['working_seconds'], 23)
            other = self.service.dispatch('current', {'provider': 'codex', 'session_id': 'other'})
            self.assertEqual(other['working_seconds'], 0)
            self.assertEqual(self.call('open')['working_seconds'], 23)
        self.backend.busy = False
        self.assertEqual(self.call('current')['working_seconds'], 0)
        self.assertNotIn(('codex', 'session-1'), self.service.working_since)
    def test_selection_history_and_create(self):
        self.assertEqual(len(self.service.dispatch("providers", {})["providers"]), 3)
        self.assertEqual(self.call("sessions")["sessions"][0]["id"], "session-1")
        self.assertIn("历史内容", self.call("open")["text"])
        created = self.call("create", request_id="new-1")
        self.assertEqual(created["session_id"], "new-session")
        self.assertEqual(self.call("create", request_id="new-1"), created)
        self.assertEqual(self.backend.calls.count("create"), 1)

    def test_status_text_retry_preserves_working_timer(self):
        with patch('cloud_pc.host_program.llm_service.time.monotonic', return_value=100):
            self.call('send', request_id='status-send', text='test')
        self.backend.current = lambda sid: snapshot([], True, run_status={
            'state': 'retrying', 'label': '重试中', 'attempt': 2})
        with patch('cloud_pc.host_program.llm_service.time.monotonic', return_value=132):
            result = self.call('current')
        self.assertEqual(result['status_text'], '重试中 · 第2次（32s）')
        self.assertEqual(result['working_seconds'], 32)

    def test_read_disconnect_does_not_send_again(self):
        def fail(sid): raise ConnectionError('connection lost')
        self.backend.current = fail
        result = self.call('current')
        self.assertFalse(result['ok'])
        self.assertEqual(result['run_status']['state'], 'disconnected')
        self.assertEqual(self.backend.calls, [])
    def test_send_once_stop_once_and_busy_queue(self):
        submitted = self.call("send", request_id="send-1", text="你好\n第二行")
        self.assertTrue(submitted["ok"])
        self.assertEqual(self.call("send", request_id="send-1", text="你好\n第二行"), submitted)
        self.assertEqual(self.call("send", request_id="send-2", text="另一条")["stage"], 'queued')
        self.assertTrue(self.call("stop", request_id="stop-1")["ok"])
        self.assertEqual(self.call("stop", request_id="stop-2")["stage"], "ignored")
        self.assertEqual(len([x for x in self.backend.calls if isinstance(x,tuple) and x[0]=="stop"]), 1)
        self.backend.busy = False
        self.call("current")
        self.assertTrue(self.call("send", request_id="send-3", text="新回合")["ok"])
        self.assertEqual(self.call("stop", request_id="stop-3")["stage"], "stopped")

    def test_busy_message_waits_until_next_turn_and_deduplicates(self):
        self.backend.busy = True
        first = self.call('send', request_id='queue-1', text='next')
        self.assertEqual(first['stage'], 'queued')
        self.assertEqual(self.call('send', request_id='queue-1', text='next')['stage'], 'queued')
        self.service.process_queued()
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.call('current')['queued_count'], 1)
        self.backend.busy = False
        self.service.process_queued()
        self.assertEqual(self.backend.calls, [('send', 'session-1', 'next')])
        self.assertEqual(self.call('send', request_id='queue-1', text='next')['stage'], 'submitted')
        self.service.process_queued()
        self.assertEqual(len(self.backend.calls), 1)

    def test_queue_submission_failure_not_replayed(self):
        self.backend.busy = True
        self.call('send', request_id='queue-fail', text='next')
        self.backend.busy = False
        def fail(sid, text):
            self.backend.calls.append('attempt')
            raise TimeoutError('unknown')
        self.backend.send = fail
        self.service.process_queued()
        self.service.process_queued()
        self.assertEqual(self.backend.calls, ['attempt'])
        self.assertEqual(self.call('send', request_id='queue-fail', text='next')['stage'], 'unknown')
    def test_conflict_and_invalid_inputs(self):
        self.call("send", request_id="same-id", text="A")
        self.assertIn("conflict", self.call("send", request_id="same-id", text="B")["error"])
        for data in [{"text": "\0"}, {"text": " "}, {"text": "a" * 16385}, {"request_id": "../bad"}]:
            self.assertFalse(self.call("send", request_id=data.pop("request_id", "bad"), **data)["ok"])
        self.assertFalse(self.call("current", session_id="../bad")["ok"])
        self.assertFalse(self.call("sessions", provider="unknown")["ok"])
    def test_target_is_explicit(self):
        other = FakeBackend(); self.service.backends["dsh"] = other
        self.call("open", provider="dsh", session_id="dsh-1")
        self.call("send", request_id="target-send", text="对 Codex", session_id="codex-2")
        self.assertIn(("send", "codex-2", "对 Codex"), self.backend.calls)
        self.assertNotIn("send", str(other.calls))
    def test_request_cache_survives_restart_and_unknown_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "state.db"
            s = LLMService(backends={"codex": self.backend}, state_path=db)
            body = {"provider": "codex", "session_id": "session-1", "request_id": "persist", "text": "一次"}
            first = s.dispatch("send", body); s.close()
            s = LLMService(backends={"codex": self.backend}, state_path=db)
            self.assertEqual(s.dispatch("send", body), first)
            s.db.execute("UPDATE requests SET result=NULL WHERE id='persist'"); s.db.commit()
            self.assertEqual(s.dispatch("send", body)["stage"], "unknown")
            s.close()
            self.assertEqual(self.backend.calls.count(("send", "session-1", "一次")), 1)
    def test_read_does_not_create_or_send(self):
        self.call("sessions"); self.call("current")
        self.assertEqual(self.backend.calls, [])
    def test_remote_api_is_rejected(self):
        for url in ["http://10.0.0.7:4096", "https://example.com", "http://secret@localhost:4096"]:
            with self.assertRaises(ValueError): LocalHTTP({"url": url})
    def test_snapshot_is_bounded(self):
        result = snapshot([{"role": "user", "text": "x" * 70000}])
        self.assertTrue(result["truncated"]); self.assertEqual(len(result["text"]), 65536)
    def test_loopback_slave_routes(self):
        for op in ["providers", "sessions", "open", "create", "current", "send", "stop"]:
            self.assertEqual(ROUTES[("POST", "/api/management/llm/" + op)], "llm_" + op)


class APITests(unittest.IsolatedAsyncioTestCase):
    async def test_host_api_uses_integrated_backend_and_guards_access(self):
        api = HostAPI()
        backend = FakeBackend()
        api._llm = LLMService(backends={"codex": backend}, state_path=":memory:")
        server = await asyncio.start_server(api._handle_http, "127.0.0.1", 0)
        client = HostClient("http://127.0.0.1:" + str(server.sockets[0].getsockname()[1]))
        try:
            self.assertTrue((await asyncio.to_thread(client.llm, "sessions", "codex"))["ok"])
            self.assertTrue((await asyncio.to_thread(client.llm, "send", "codex", "session-1", request_id="http-1", text="测试"))["ok"])
            self.assertEqual(backend.calls, [("send", "session-1", "测试")])
            other = HostClient(client.base_url)
            await asyncio.to_thread(other.activate)
            denied = await asyncio.to_thread(client.llm, "send", "codex", "session-1", request_id="http-2", text="不能发送")
            self.assertEqual(denied["code"], "management_inactive")
        finally:
            server.close(); await server.wait_closed(); await api.close()
    async def test_cldc_request_authorization_and_activity(self):
        api = HostAPI()
        backend = FakeBackend()
        api._llm = LLMService(backends={"codex": backend}, state_path=":memory:")
        client = SimpleNamespace(slave_id="phone", address="10.0.0.7:9527", host_ip="10.0.0.7")
        api.management.owner = {"session_id": "a" * 32, "address": client.address}
        api.request = AsyncMock(return_value={"ok": True})
        channel = AndroidCurrentChannel(api, client, None)
        body = {"provider": "codex", "session_id": "session-1", "request_id": "cldc-1", "text": "测试"}
        await channel.management({"send_id": "b" * 32, "operation": "llm_send", "payload": body})
        self.assertEqual(api.request.call_args.args[2]["result"]["code"], "management_inactive")
        body["_management_session"] = "a" * 32
        await channel.management({"send_id": "c" * 32, "operation": "llm_send", "payload": body})
        self.assertTrue(api.request.call_args.args[2]["result"]["ok"])
        records = api.activity.snapshot([])["records"]
        self.assertTrue(any(r["ip"]=="10.0.0.7" and "LLM codex send" in r["action"] for r in records))
        await api.close()


class BackendWireTests(unittest.TestCase):
    def test_dsh_and_opencode_api_contracts(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self): self.reply(None)
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                self.reply(body)
            def reply(self, body):
                calls.append((self.command, self.path, body))
                path = self.path.split("?")[0]
                if path.startswith("/api/session."):
                    method = path[5:]
                    values = {
                        "session.list": {"items": [{"sessionId": "dsh-test", "running": False, "updatedAt": 1000}]},
                        "session.create": {"sessionId": "dsh-new"},
                        "session.history": {"events": [{"event": {"type": "assistant/message", "data": {
                            "message": {"content": [{"type": "text", "text": "DSH 回复"}]}}}}]},
                        "session.prompt": {"accepted": True}, "session.cancel": {}}
                    result = {"result": {"ok": True, "value": values[method]}}
                elif path == "/session":
                    result = [{"id": "oc-test", "title": "测试", "time": {"updated": 1000}}] if self.command=="GET" else {"id": "oc-new"}
                elif path == "/session/status": result = {"oc-test": {"type": "busy"}}
                elif path.endswith("/message"):
                    result = [{"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "OpenCode 回复"}]}]
                else: result = {}
                payload = json.dumps(result).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:" + str(server.server_port)
        try:
            dsh = DshBackend({"url": url, "cwd": "/tmp"})
            self.assertEqual(dsh.sessions()[0][0]["id"], "dsh-test")
            self.assertEqual(dsh.create(), "dsh-new")
            self.assertIn("DSH 回复", dsh.open("dsh-test")["text"])
            dsh.send("dsh-test", "你好"); dsh.stop("dsh-test")
            oc = OpenCodeBackend({"url": url, "cwd": "/tmp/workspace"})
            self.assertEqual(oc.sessions()[0][0]["id"], "oc-test")
            self.assertEqual(oc.create(), "oc-new")
            self.assertTrue(oc.current("oc-test")["busy"])
            oc.send("oc-test", "你好"); oc.stop("oc-test")
            prompt = next(b for _,p,b in calls if p=="/api/session.prompt")
            self.assertEqual(prompt["payload"]["mode"], "queue")
            self.assertEqual(prompt["payload"]["content"], [{"type":"text", "text":"你好"}])
            post = next(b for _,p,b in calls if p.startswith("/session/oc-test/prompt_async"))
            self.assertEqual(post["parts"], [{"type":"text", "text":"你好"}])
            self.assertTrue(any("directory=" in p for _,p,_ in calls if p.startswith("/session")))
        finally:
            server.shutdown(); server.server_close()


if __name__ == "__main__": unittest.main()
