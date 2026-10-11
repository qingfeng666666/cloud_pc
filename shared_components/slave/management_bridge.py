"""Loopback-only UI API. Requests travel on the host-established CLDC session."""
import json
import os
import socket
import struct
import threading
import uuid
from concurrent.futures import Future, TimeoutError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

CAPABILITY = "management_bridge_v1"
PUSH_CAPABILITY = "management_push_v1"
ROUTES = {("GET", "/api/management/current"): "current",
          ("POST", "/api/management/slaves"): "slaves",
          ("GET", "/api/management/activity"): "activity",
          ("GET", "/api/slaves"): "slaves",
          ("POST", "/api/management/current/send"): "send",
          ("POST", "/api/management/current/stop"): "stop",
          ("POST", "/api/cloud/screen/capture"): "screenshot"}
LLM_OPERATIONS = {"providers", "sessions", "create", "open", "current", "send", "stop"}
ROUTES.update({("POST", "/api/management/llm/" + op): "llm_" + op for op in LLM_OPERATIONS})


class ManagementBridge:
    def __init__(self, slave_port=9527):
        self.slave_port = slave_port
        self.host_address = os.environ.get("CLOUD_MANAGEMENT_HOST", "10.0.0.2")
        self.registration_port = int(os.environ.get("CLOUD_MANAGEMENT_PORT", "19526"))
        self.lock = threading.Lock()
        self.senders = {}
        self.pending = {}
        self.connections = set()
        self.server = None

    def connected(self, owner, host_ip=None, sender=None):
        with self.lock:
            self.connections.add(owner)
            if sender: self.senders[owner] = sender
            if host_ip: self.host_address = host_ip

    def register(self, operation, payload):
        data = json.dumps({"operation": operation, "session_id": payload.get("session_id"),
                           "slave_port": self.slave_port}).encode()
        try:
            import ipaddress
            target = str(ipaddress.IPv4Address(payload.get("host_ip", self.host_address)))
            with socket.create_connection((target, self.registration_port), timeout=5) as sock:
                sock.sendall(struct.pack("!4sHHI", b"CLDC", 1, 0x0701, len(data)) + data)
                def read_exact(n):
                    result = bytearray()
                    while len(result) < n:
                        chunk = sock.recv(n - len(result))
                        if not chunk: raise OSError("注册响应不完整")
                        result.extend(chunk)
                    return bytes(result)
                magic, version, kind, length = struct.unpack("!4sHHI", read_exact(12))
                if (magic, version, kind) != (b"CLDC", 1, 0x0781) or not 0 < length <= 4096:
                    raise ValueError("注册响应格式错误")
                return json.loads(read_exact(length))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": "管理注册失败：" + str(exc)[:120]}

    def disconnected(self, owner):
        with self.lock:
            self.connections.discard(owner)
            self.senders.pop(owner, None)
            if not self.connections:
                for future in self.pending.values():
                    if not future.done():
                        future.set_result({"ok": False, "error": "主机连接已断开；未自动重发"})

    def request(self, operation, payload, timeout=30):
        request_id = uuid.uuid4().hex
        future = Future()
        with self.lock:
            if not self.senders:
                return {"ok": False, "error": "本机从机尚未连接支持主动请求的云电脑主机"}
            if len(self.pending) >= 16:
                return {"ok": False, "error": "管理请求队列已满"}
            self.pending[request_id] = future
            sender = next(iter(self.senders.values()))
        try:
            sender({"event": "host_management_request", "send_id": request_id,
                    "operation": operation, "payload": payload})
            return future.result(timeout=timeout)
        except TimeoutError:
            return {"ok": False, "error": "主机结果未确认；未自动重发"}
        except Exception as exc:
            return {"ok": False, "error": "管理请求发送失败；未自动重发：" + str(exc)[:120]}
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def resolve(self, payload):
        with self.lock:
            future = self.pending.get(payload.get("request_id"))
            result = payload.get("result")
            if future and not future.done() and isinstance(result, dict):
                future.set_result(result)

    def start(self, port=19528):
        bridge = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.handle_api("GET")

            def do_POST(self):
                self.handle_api("POST")

            def handle_api(self, method):
                status, result = 200, {}
                try:
                    if self.headers.get("Origin") or self.headers.get("Transfer-Encoding"):
                        raise ValueError("不允许浏览器跨域请求或分块请求")
                    path = urlparse(self.path).path
                    if path == "/health" and method == "GET":
                        result = {"ok": True, "service": "cloud-pc-slave-management",
                                  "host_connected": bool(bridge.connections)}
                    else:
                        operation = ROUTES.get((method, path))
                        if method == "POST" and path in {"/api/management/session/activate", "/api/management/session/release"}:
                            operation = path.rsplit("/", 1)[1]
                        if not operation:
                            raise ValueError("不支持的本机管理操作")
                        length = int(self.headers.get("Content-Length", "0"))
                        if not 0 <= length <= 32768:
                            raise ValueError("请求过大")
                        self.connection.settimeout(5)
                        data = self.rfile.read(length)
                        if len(data) != length:
                            raise ValueError("请求不完整")
                        payload = json.loads(data) if data else {}
                        if not isinstance(payload, dict):
                            raise ValueError("JSON必须为对象")
                        if operation in {"activate", "release"}:
                            result = bridge.register(operation, payload)
                        else:
                            payload["_management_session"] = self.headers.get("X-Management-Session")
                            result = bridge.request(operation, payload)
                except Exception as exc:
                    status, result = 400, {"ok": False, "error": str(exc)[:160]}
                data = json.dumps(result, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass
        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, name="slave-local-management", daemon=True).start()

    def close(self):
        for owner in list(self.connections):
            self.disconnected(owner)
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
