"""Local application APIs; never configure models, credentials or permissions."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import socket
import struct
import subprocess
import threading
import time
import shutil
import urllib.request
import uuid
from urllib.parse import quote, urlsplit
from pathlib import Path

LOG = logging.getLogger("cloud.host.llm")


STATUS_LABELS = {"idle": "空闲", "working": "Working", "executing": "正在执行命令／工具",
                 "waiting_approval": "等待批准", "retrying": "重试中", "disconnected": "连接中断",
                 "completed": "已完成", "stopped": "已停止", "failed": "失败"}


def run_status(state, **details):
    return {"state": state, "label": STATUS_LABELS[state], **details}


def tool_running(parts):
    return any(p.get("type") == "tool" and
               (p.get("state") or {}).get("status") == "running" for p in parts)


def dsh_run_status(events, busy, has_messages):
    state = "working" if busy else "completed" if has_messages else "idle"
    tools, approvals = set(), set()
    details = {}
    for row in events:
        event = row.get("event") or {}
        data = event.get("data") or {}
        kind = event.get("type")
        if kind == "turn/start":
            tools.clear(); approvals.clear(); state = "working"; details = {}
        elif kind == "tool/call": tools.add(data.get("callId"))
        elif kind == "tool/result":
            tools.discard((data.get("message") or {}).get("source", {}).get("callId"))
        elif kind == "approval/asked": approvals.add(data.get("id"))
        elif kind == "approval/decided": approvals.discard(data.get("id"))
        elif kind == "llm/retry":
            state = "retrying"
            details = {"attempt": data.get("retry", 0)}
        elif kind in {"llm/retry-started", "assistant/chunk", "assistant/message", "step/start"}:
            state = "working"; details = {}
        elif kind == "turn/end":
            tools.clear(); approvals.clear(); details = {}
            reason = (data.get("reason") or {}).get("kind")
            state = "stopped" if reason in {"interrupted", "aborted"} else "failed" if reason == "error" else "completed"
    if busy:
        if approvals: state = "waiting_approval"
        elif tools: state = "executing"
        elif state in {"idle", "stopped", "completed", "failed"}: state = "working"
    elif state in {"working", "executing", "waiting_approval", "retrying"}: state = "idle"
    return run_status(state, **details)


def block_text(blocks):
    return "\n".join(b.get("text", "") for b in (blocks or [])
                     if isinstance(b, dict) and b.get("type") in {"text", "input_text"})


def visible_blocks(blocks, role="assistant"):
    """Only explicit public API fields; never encrypted/raw hidden reasoning."""
    messages = []
    for block in blocks or []:
        kind = block.get("type")
        if kind in {"text", "input_text", "reasoning"} and block.get("text"):
            messages.append({"role": "思考摘要" if kind == "reasoning" else role,
                             "text": block["text"]})
        elif kind in {"tool", "tool_use", "tool-call"}:
            state = block.get("state") or {}
            if not isinstance(state, dict): state = {}
            name = block.get("tool") or block.get("name") or "工具"
            status = state.get("status", "执行中")
            output = state.get("output", "")
            messages.append({"role": "工具执行", "text": f"{name} · {status}\n{str(output)[:8000]}".rstrip()})
    return messages


def codex_item(item):
    kind = item.get("type")
    if kind == "userMessage": return [{"role": "user", "text": block_text(item.get("content"))}]
    if kind == "agentMessage": return [{"role": "assistant", "text": item.get("text", "")}]
    if kind == "reasoning":
        summary = item.get("summary") or []
        text = "\n".join(s if isinstance(s, str) else s.get("text", "") for s in summary)
        return [{"role": "思考摘要", "text": text}] if text else []
    if kind == "plan": return [{"role": "计划", "text": item.get("text", "")}]
    if kind in {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "collabToolCall", "webSearch"}:
        name = item.get("command") or item.get("tool") or item.get("query") or kind
        text = f"{name} · {item.get('status', '执行中')}"
        if item.get("exitCode") is not None: text += f" · exit={item['exitCode']}"
        if item.get("aggregatedOutput"): text += "\n" + str(item["aggregatedOutput"])[-8000:]
        return [{"role": "工具执行", "text": text}]
    return []


def snapshot(messages, busy=False, **extra):
    # Bound what travels on CLDC/Binder, not the provider's durable history.
    text = "\n\n".join(f"{m['role']}:\n{m['text']}" for m in messages)
    truncated = len(text) > 65536
    text = text[-65536:]
    return {"ok": True, "available": True, "text": text, "busy": busy,
            "revision": hashlib.sha256(text.encode()).hexdigest(),
            "truncated": truncated, **extra}


class LocalHTTP:
    def __init__(self, config):
        self.config = config
        self.url = config["url"].rstrip("/")
        parts = urlsplit(self.url)
        if (parts.scheme != "http" or parts.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parts.username or parts.password or parts.query or parts.fragment):
            raise ValueError("LLM API 必须是主机本地 HTTP 地址")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.start_lock = threading.Lock()
        self.service_process = None

    def ensure_ready(self):
        """Selection starts a missing local service; never replay an API mutation."""
        parts = urlsplit(self.url)
        address = (parts.hostname, parts.port or 80)
        def listening():
            try:
                with socket.create_connection(address, timeout=0.3): return True
            except ConnectionRefusedError: return False
        with self.start_lock:
            if listening(): return
            command = self.config.get("start_command")
            if not command:
                raise RuntimeError("本地 LLM 服务未启动；请在主机配置 start_command")
            if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
                raise ValueError("start_command 必须是命令参数数组")
            command = list(command)
            executable = shutil.which(command[0])
            if not executable and command[0] == "opencode":
                candidate = Path.home() / ".opencode/bin/opencode"
                if candidate.is_file(): executable = str(candidate)
            if executable: command[0] = executable
            directory = Path.home() / ".local/state/cloud_pc/llm_logs"
            directory.mkdir(parents=True, exist_ok=True)
            log_path = directory / (type(self).__name__ + ".log")
            if self.service_process is None or self.service_process.poll() is not None:
                with log_path.open("ab") as output:
                    if os.name != "nt": log_path.chmod(0o600)
                    self.service_process = subprocess.Popen(command, cwd=self.config.get("cwd"),
                        stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                        start_new_session=(os.name != "nt"))
                LOG.info("LLM_SERVICE_START backend=%s pid=%s log=%s", type(self).__name__, self.service_process.pid, log_path)
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if listening():
                    LOG.info("LLM_SERVICE_READY backend=%s", type(self).__name__)
                    return
                if self.service_process.poll() is not None:
                    raise RuntimeError("LLM 服务启动失败，日志：" + str(log_path))
                time.sleep(0.15)
            raise RuntimeError("LLM 服务启动超时，日志：" + str(log_path))

    def http(self, path, data=None, method=None):
        self.ensure_ready()
        headers = {"Content-Type": "application/json"}
        # Optional local API authentication is provisioned by the host user.
        env = self.config.get("password_env")
        if env:
            import base64
            password = os.environ.get(env)
            if password is None: raise RuntimeError("主机未设置本地 API 认证环境变量")
            auth = f"{self.config.get('username', 'opencode')}:{password}"
            headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
        payload = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        req = urllib.request.Request(self.url + path, data=payload, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=15) as response:
                raw = response.read(32 * 1024 * 1024 + 1)
                if len(raw) > 32 * 1024 * 1024: raise RuntimeError("LLM API 响应过大")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            # Never echo upstream response bodies, which may contain secrets.
            raise RuntimeError(f"本地 LLM API HTTP {exc.code}") from exc

    def close(self): pass


class DshBackend(LocalHTTP):
    def rpc(self, method, body):
        response = self.http("/api/" + method, {"type": "client-request", "rpcId": str(uuid.uuid4()),
                            "method": method, "payload": body})["result"]
        if not response.get("ok"):
            raise RuntimeError((response.get("error") or {}).get("message", "DSH API 调用失败"))
        return response.get("value") or {}

    def sessions(self, cursor=None):
        items = self.rpc("session.list", {}).get("items", [])
        items.sort(key=lambda i: i.get("updatedAt", 0), reverse=True)
        start = int(cursor or 0)
        rows = []
        for item in items[start:start + 50]:
            projections = item.get("projections", {}).get("values", {})
            title = projections.get("title") or projections.get("session/title") or ""
            if isinstance(title, dict): title = title.get("title", "")
            rows.append({"id": item["sessionId"], "title": title or item.get("cwd") or item["sessionId"],
                         "updated_at": item.get("updatedAt", 0) / 1000, "busy": item.get("running", False)})
        return rows, str(start + 50) if len(items) > start + 50 else None

    def create(self):
        body = {"cwd": self.config["cwd"]} if self.config.get("cwd") else {}
        return self.rpc("session.create", body)["sessionId"]

    def open(self, sid): return self.current(sid)

    def current(self, sid):
        items = self.rpc("session.list", {}).get("items", [])
        item = next((i for i in items if i["sessionId"] == sid), None)
        if item is None: raise ValueError("DSH 会话不存在")
        history = self.rpc("session.history", {"sessionId": sid, "maxMessages": 20})
        messages = []
        partial = {}
        for row in history.get("events", []):
            e = row.get("event", {})
            data = e.get("data") or {}
            kind = e.get("type")
            key = (str(data.get("turn")), str(data.get("step")))
            if kind == "assistant/chunk":
                chunk = data.get("chunk") or {}
                blocks = partial.setdefault(key, {})
                index = chunk.get("index", 0)
                if chunk.get("type") == "block-start":
                    blocks[index] = {"type": chunk.get("blockType"), "text": ""}
                elif chunk.get("type") == "text-delta":
                    block = blocks.setdefault(index, {"type": "text", "text": ""})
                    block["text"] = (block["text"] + chunk.get("text", ""))[-65536:]
                elif chunk.get("type") == "block-end" and isinstance(chunk.get("block"), dict):
                    blocks[index] = chunk["block"]
                continue
            if kind == "tool/call":
                messages.append({"role": "工具执行", "text": str(data.get("name", "工具")) + " · 已调用"})
                continue
            if kind == "tool/result":
                msg = data.get("message") or {}
                text = block_text(msg.get("content")) if isinstance(msg, dict) else str(msg)
                messages.append({"role": "工具结果", "text": ("失败" if data.get("error") else "完成") + "\n" + text[-8000:]})
                continue
            if kind not in {"user/message", "assistant/message"}: continue
            msg = data.get("message") or data
            if msg.get("source", {}).get("kind") == "plugin": continue
            if kind == "assistant/message": partial.pop(key, None)
            messages.extend(visible_blocks(msg.get("content", []), kind.split("/")[0]))
        for blocks in partial.values():
            messages.extend(visible_blocks(list(blocks.values())))
        busy = bool(item.get("running"))
        return snapshot(messages, busy, run_status=dsh_run_status(history.get("events", []), busy, bool(messages)),
                        history_more=history.get("hasMore", False))

    def send(self, sid, text):
        self.rpc("session.prompt", {"sessionId": sid, "mode": "queue",
                 "content": [{"type": "text", "text": text}]})

    def stop(self, sid): self.rpc("session.cancel", {"sessionId": sid})


class OpenCodeBackend(LocalHTTP):
    def path(self, path):
        # Directory scopes OpenCode requests to the preconfigured workspace.
        return path + ("?directory=" + quote(self.config["cwd"], safe="") if self.config.get("cwd") else "")

    def sessions(self, cursor=None):
        rows = self.http(self.path("/session"))
        rows.sort(key=lambda x: x.get("time", {}).get("updated", 0), reverse=True)
        start = int(cursor or 0)
        return [{"id": r["id"], "title": r.get("title") or r["id"],
                 "updated_at": r.get("time", {}).get("updated", 0) / 1000} for r in rows[start:start + 50]], (
                 str(start + 50) if len(rows) > start + 50 else None)

    def create(self): return self.http(self.path("/session"), {})["id"]
    def open(self, sid): return self.current(sid)

    def current(self, sid):
        status = self.http(self.path("/session/status")).get(sid, {})
        rows = self.http(self.path("/session/" + quote(sid, safe="") + "/message"))
        messages = [message for r in rows for message in
                    visible_blocks(r.get("parts"), r.get("info", {}).get("role", "assistant"))]
        busy = status.get("type", "idle") != "idle"
        state = "working" if busy else "completed" if messages else "idle"
        details = {}
        if status.get("type") == "retry":
            state = "retrying"
            details = {k: status[k] for k in ("attempt", "next") if k in status}
        elif busy and rows and tool_running(rows[-1].get("parts") or []): state = "executing"
        if rows and not busy and rows[-1].get("info", {}).get("error"):
            error = rows[-1]["info"]["error"]
            state = "stopped" if error.get("name") == "MessageAbortedError" else "failed"
        try:
            permissions = self.http(self.path("/permission"))
            if isinstance(permissions, list) and any(p.get("sessionID") == sid for p in permissions):
                state = "waiting_approval"; busy = True
        except RuntimeError as exc:
            if "HTTP 404" not in str(exc): raise
        return snapshot(messages, busy, run_status=run_status(state, **details))

    def send(self, sid, text):
        self.http(self.path("/session/" + quote(sid, safe="") + "/prompt_async"),
                  {"parts": [{"type": "text", "text": text}]})

    def stop(self, sid): self.http(self.path("/session/" + quote(sid, safe="") + "/abort"), {})


class DaemonSocket:
    """RFC6455 over a local Unix socket, as used by Codex's managed daemon."""
    def __init__(self, path):
        import base64
        self.send_lock = threading.Lock()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        try:
            self.sock.connect(path)
            key = base64.b64encode(os.urandom(16)).decode()
            self.sock.sendall(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: " + key + "\r\n\r\n").encode())
            header = bytearray()
            while not header.endswith(b"\r\n\r\n"):
                header.extend(self.exact(1))
                if len(header) > 16384: raise RuntimeError("Codex 握手响应过大")
            if not bytes(header).startswith(b"HTTP/1.1 101 "): raise RuntimeError("Codex daemon WebSocket 握手失败")
            expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            headers = dict(line.split(":", 1) for line in header.decode().split("\r\n")[1:] if ":" in line)
            actual = next((v.strip() for k,v in headers.items() if k.lower() == "sec-websocket-accept"), "")
            if actual != expected: raise RuntimeError("Codex daemon 握手校验失败")
            self.sock.settimeout(None)
        except BaseException:
            self.sock.close(); raise

    def exact(self, n):
        data = bytearray()
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk: raise EOFError("Codex socket closed")
            data.extend(chunk)
        return bytes(data)

    def send(self, payload, opcode=1):
        data = payload.encode() if isinstance(payload, str) else payload
        length = len(data)
        header = bytes([0x80 | opcode, 0x80 | (length if length < 126 else 126 if length < 65536 else 127)])
        if length >= 126: header += struct.pack("!H" if length < 65536 else "!Q", length)
        mask = os.urandom(4)
        with self.send_lock:
            self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i,b in enumerate(data)))

    def messages(self):
        parts = bytearray()
        while True:
            a,b = self.exact(2)
            n = b & 127
            if n == 126: n = struct.unpack("!H", self.exact(2))[0]
            elif n == 127: n = struct.unpack("!Q", self.exact(8))[0]
            if n + len(parts) > 64 * 1024 * 1024: raise RuntimeError("Codex frame too large")
            if b & 128: raise RuntimeError("Unexpected masked server frame")
            payload = self.exact(n)
            opcode = a & 15
            if opcode == 8: return
            if opcode == 9: self.send(payload, 10); continue
            if opcode == 10: continue
            if opcode not in {0,1}: raise RuntimeError("Unsupported Codex frame")
            parts.extend(payload)
            if a & 128:
                yield parts.decode("utf-8")
                parts.clear()

    def close(self):
        try: self.sock.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        self.sock.close()


class CodexBackend:
    """Persistent JSON-RPC connection to the user's configured app-server.

    Default connects to the running daemon's local WebSocket Unix socket.
    No shell expansion, auto-login, config override, or permission escalation.
    """
    def __init__(self, config):
        self.config = config
        self.process = None
        self.socket = None
        self.connected = False
        self.init_lock = threading.Lock()
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.pending = {}
        self.server_requests = {}
        self.resumed = set()
        self.turns = {}
        self.failure = None
        self.live_items = {}
        self.run_states = {}

    def start(self):
        with self.init_lock:
            if self.connected: return
            with self.lock:
                self.failure = None
                self.resumed.clear()
                self.server_requests.clear()
                path = self.config.get("socket")
                if path and os.name != "nt":
                    if path == "auto":
                        path = str(Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "app-server-control/app-server-control.sock")
                    self.socket = DaemonSocket(path)
                    source = self.socket.messages()
                else:
                    command = self.config.get("command", ["codex", "app-server", "--stdio"])
                    if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
                        raise ValueError("Codex command 必须是主机配置的参数数组")
                    self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1, cwd=self.config.get("cwd"))
                    source = self.process.stdout
                    threading.Thread(target=self.drain_errors, args=(self.process,), daemon=True).start()
                self.connected = True
                threading.Thread(target=self.read, args=(source,), daemon=True, name="llm-codex-rpc").start()
            try:
                self.rpc("initialize", {"clientInfo": {"name": "cloud_pc_llm", "version": "1.0.0"},
                                      "capabilities": {"experimentalApi": True}}, ensure=False)
                self.write({"method": "initialized"})
            except BaseException:
                self.close(); raise

    def drain_errors(self, process):
        for _ in process.stderr: pass  # Do not expose tokens or confidential provider logs.

    def write(self, message):
        with self.write_lock:
            if self.socket:
                self.socket.send(json.dumps(message, ensure_ascii=False)); return
            if not self.process or self.process.poll() is not None: raise RuntimeError("Codex API 连接已关闭")
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def read(self, source):
        try:
            for line in source:
                packet = json.loads(line)
                with self.lock:
                    if "id" in packet and "method" not in packet:
                        waiter = self.pending.get(packet["id"])
                        if waiter: waiter.put(packet)
                    elif "id" in packet:
                        # Explicit user approval remains mandatory. Never auto-accept.
                        self.server_requests[str(packet["id"])] = packet
                    else:
                        params = packet.get("params", {})
                        sid = params.get("threadId")
                        self.observe(packet.get("method"), params)
                        if sid and packet.get("method") in {"turn/started", "turn/completed"}:
                            self.turns[sid] = params.get("turn", {}).get("id") if packet["method"] == "turn/started" else None
                        if packet.get("method") == "serverRequest/resolved":
                            self.server_requests.pop(str(params.get("requestId")), None)
        except Exception:
            if self.connected: LOG.warning("Codex JSON-RPC connection ended")
        finally:
            with self.lock:
                self.connected = False
                self.failure = "Codex API 连接断开；未自动重发"
                for waiter in self.pending.values():
                    waiter.put({"error": {"message": self.failure}})

    def observe(self, method, params):
        sid, tid = params.get("threadId"), params.get("turnId")
        if not sid: return
        with self.lock:
            if method == "turn/started": self.run_states[sid] = run_status("working")
            elif method == "turn/completed":
                state = params.get("turn", {}).get("status")
                self.run_states[sid] = run_status({"completed": "completed", "interrupted": "stopped",
                                                  "failed": "failed"}.get(state, "idle"))
            elif method == "error":
                self.run_states[sid] = run_status("retrying" if params.get("willRetry") else "failed")
            elif method in {"item/started", "item/completed"}:
                kind = (params.get("item") or {}).get("type")
                if kind in {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch"}:
                    self.run_states[sid] = run_status("executing" if method == "item/started" else "working")
            elif method in {"item/agentMessage/delta", "item/reasoning/summaryTextDelta"}:
                self.run_states[sid] = run_status("working")
            if method == "turn/started":
                self.live_items[sid] = {"turn": params.get("turn", {}).get("id"), "items": {}}
            entry = self.live_items.setdefault(sid, {"turn": tid, "items": {}})
            if len(self.live_items) > 64: self.live_items.pop(next(iter(self.live_items)))
            if tid and entry["turn"] != tid:
                entry = self.live_items[sid] = {"turn": tid, "items": {}}
            items = entry["items"]
            if method in {"item/started", "item/completed"}:
                item = params.get("item") or {}
                if item.get("id"): items[item["id"]] = dict(item)
            elif method in {"item/agentMessage/delta", "item/reasoning/summaryTextDelta", "item/commandExecution/outputDelta", "item/plan/delta"}:
                ident = params.get("itemId")
                if not ident: return
                kind = {"item/agentMessage/delta": "agentMessage", "item/reasoning/summaryTextDelta": "reasoning",
                        "item/commandExecution/outputDelta": "commandExecution", "item/plan/delta": "plan"}[method]
                item = items.setdefault(ident, {"id": ident, "type": kind})
                delta = params.get("delta", "")
                if kind == "reasoning":
                    index = params.get("summaryIndex", 0)
                    if not isinstance(index, int) or not 0 <= index < 64: return
                    summary = item.setdefault("summary", [])
                    while len(summary) <= index: summary.append("")
                    summary[index] = (summary[index] + delta)[-16000:]
                else:
                    field = "aggregatedOutput" if kind == "commandExecution" else "text"
                    item[field] = (item.get(field, "") + delta)[-65536:]
            while len(items) > 256: items.pop(next(iter(items)))

    def rpc(self, method, params, ensure=True):
        if ensure: self.start()
        ident = uuid.uuid4().hex
        waiter = queue.Queue()
        with self.lock: self.pending[ident] = waiter
        try:
            self.write({"id": ident, "method": method, "params": params})
            try: response = waiter.get(timeout=15)
            except queue.Empty: raise RuntimeError("Codex API 超时；执行结果未确认，未重发")
            if "error" in response: raise RuntimeError(response["error"].get("message", "Codex API 错误"))
            return response.get("result") or {}
        finally:
            with self.lock: self.pending.pop(ident, None)

    def sessions(self, cursor=None):
        body = {"limit": 50, "sortKey": "updated_at"}
        if cursor: body["cursor"] = cursor
        response = self.rpc("thread/list", body)
        return [{"id": t["id"], "title": t.get("name") or t.get("preview") or t["id"],
                 "updated_at": t.get("updatedAt"), "cwd": t.get("cwd")} for t in response.get("data", [])], response.get("nextCursor")

    def create(self):
        body = {"cwd": self.config["cwd"]} if self.config.get("cwd") else {}
        sid = self.rpc("thread/start", body)["thread"]["id"]
        self.resumed.add(sid)
        return sid

    def open(self, sid):
        if sid not in self.resumed:
            # Resume also subscribes this API connection to an already-loaded thread.
            try:
                self.rpc("thread/resume", {"threadId": sid, "excludeTurns": True})
            except RuntimeError as exc:
                if "no rollout found" in str(exc):
                    raise RuntimeError("该 Codex 空会话尚未保存历史且已卸载，请新建会话") from exc
                raise
        self.resumed.add(sid)
        return self.current(sid)

    def current(self, sid):
        thread = self.rpc("thread/read", {"threadId": sid, "includeTurns": False})["thread"]
        try:
            history = self.rpc("thread/turns/list", {"threadId": sid, "limit": 20,
                "sortDirection": "desc", "itemsView": "full"})
            turns = list(reversed(history.get("data", [])))
        except RuntimeError as exc:
            if "not materialized yet" not in str(exc): raise
            history, turns = {}, []  # A valid newly-created, empty thread.
        messages = []
        turn_id = None
        with self.lock:
            live = json.loads(json.dumps(self.live_items.get(sid, {})))
        matched_live = False
        for turn in turns:
            if turn.get("status") == "inProgress": turn_id = turn["id"]
            items = {i.get("id", str(n)): i for n, i in enumerate(turn.get("items", []))}
            if turn.get("id") == live.get("turn"):
                matched_live = True
                for ident, item in live.get("items", {}).items():
                    if turn.get("status") == "inProgress" or ident not in items: items[ident] = item
            for item in items.values(): messages.extend(codex_item(item))
            if turn.get("error"):
                error = turn["error"]
                messages.append({"role": "system", "text": "本轮失败：" + str(error.get("message", error) if isinstance(error,dict) else error)[:1000]})
        if not matched_live and live.get("turn") and self.turns.get(sid) == live.get("turn"):
            for item in live.get("items", {}).values(): messages.extend(codex_item(item))
        # Read is authoritative even for turns started in another UI.
        self.turns[sid] = turn_id
        pending = [p.get("method", "") for p in self.server_requests.values()
                   if p.get("params", {}).get("threadId") == sid]
        if pending:
            messages.append({"role": "system", "text": "等待主机原生界面确认：" + ", ".join(pending)})
        busy = bool(turn_id) or thread.get("status", {}).get("type") == "active"
        state = run_status("working" if busy else "completed" if turns else "idle")
        if turns and not busy:
            state = run_status({"completed": "completed", "interrupted": "stopped", "failed": "failed"}
                               .get(turns[-1].get("status"), "idle"))
        if busy:
            with self.lock: observed = dict(self.run_states.get(sid, {}))
            if live.get("turn") == turn_id and observed.get("state") in {"working", "executing", "retrying", "failed"}: state = observed
            if not observed and turns and any(i.get("type") == "commandExecution" and
                    i.get("status") == "inProgress" for i in turns[-1].get("items", [])):
                state = run_status("executing")
        flags = thread.get("status", {}).get("activeFlags", [])
        if pending or "waitingOnApproval" in flags or "waitingOnUserInput" in flags:
            state = run_status("waiting_approval")
        return snapshot(messages, busy, run_status=state,
                        turn_id=turn_id, pending_requests=pending, history_more=bool(history.get("nextCursor")),
                        notice="有待确认请求，请在主机原生界面处理" if pending else "")

    def send(self, sid, text):
        if sid not in self.resumed: self.open(sid)
        turn = self.rpc("turn/start", {"threadId": sid, "input": [{"type": "text", "text": text}]})["turn"]
        self.turns[sid] = turn["id"]

    def stop(self, sid):
        turn_id = self.turns.get(sid)
        if not turn_id: raise RuntimeError("没有可停止的 Codex 回合")
        self.rpc("turn/interrupt", {"threadId": sid, "turnId": turn_id})

    def close(self):
        self.connected = False
        if self.socket: self.socket.close()
        self.socket = None
        if self.process and self.process.poll() is None:
            self.process.terminate()  # Only our proxy/stdio child, not the user's daemon.
        self.process = None


BACKENDS = {"codex": CodexBackend, "dsh": DshBackend, "opencode": OpenCodeBackend}
