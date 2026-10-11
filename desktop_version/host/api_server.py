"""
主机本地 HTTP API — 让外部工具通过主机的从机连接操作远程文件/命令

只监听 127.0.0.1:19527，仅本机可访问。

端口:
  GET  /api/slaves                       列出已连接从机
  GET  /api/<slave_id>/list?path=/        列出目录
  GET  /api/<slave_id>/download?path=...  下载文件
  POST /api/<slave_id>/exec               执行命令 (body: {"cmd":"...","cwd":"..."})
  POST /api/<slave_id>/upload             上传文件 (multipart)
  POST /api/<slave_id>/delete?path=...    删除文件
  POST /api/<slave_id>/mkdir?path=...     新建目录
  POST /api/<slave_id>/ue_mcp             从机本机调用 UnrealMCP
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from http import HTTPStatus
from urllib.parse import urlparse, parse_qs
from host.relay import RelayRouter
from common.rpc import RPC_CAPABILITY
from common.release import __version__
from cloud_pc.host_program.adapter_client import CodexAdapterClient
from cloud_pc.host_program.android_current import AndroidCurrentChannel

logger = logging.getLogger("host.api")

API_PORT = 19527


class HostAPI:
    """本地 HTTP 服务器，代理请求到从机"""

    def __init__(self, relay_grants=(), adapter=None, management_bind=None,
                 management_port=19526, management_interval=5, management_state=None, llm_config=None,
                 config_store=None):
        self._clients: dict[str, "SlaveClient"] = {}
        from cloud_pc.host_program.activity import ActivityFeed
        self.activity = ActivityFeed()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._request_locks: dict[tuple[str, int], asyncio.Lock] = {}
        self.relay = RelayRouter(self._clients, relay_grants)
        self._relay_unsubscribe = {}
        self._current_clients = []
        self._current_tasks = []
        self._management_channels = []
        self.adapter = adapter if adapter is not None else CodexAdapterClient()
        self._llm = None
        self.llm_config = llm_config
        self.config_store = config_store
        self._slave_settings_lock = asyncio.Lock()
        from cloud_pc.host_program.management_session import ManagementSessions
        self.management = ManagementSessions(self, management_interval, management_state)
        self.management_bind, self.management_port = management_bind, management_port

    def slave_list(self):
        return {"ok": True, "slaves": [{"id": c.slave_id, "name": c.slave_name,
            "host": c.host_ip, "port": c.port, "address": c.address,
            "connected": c.is_connected} for c in self._clients.values()]}

    async def slave_settings(self, body, source="127.0.0.1"):
        import ipaddress
        action = body.get("action", "list")
        if action == "list": return self.slave_list()
        if action not in {"add", "remove"}: raise ValueError("不支持的从机设置操作")
        if self.config_store is None: raise ValueError("主机未配置持久化入口")
        host = str(ipaddress.ip_address(str(body.get("host", "")).strip()))
        if ":" in host: raise ValueError("目前从机配置只支持 IPv4")
        port = body.get("port", 9527)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("端口应为1至65535")
        address = f"{host}:{port}"
        async with self._slave_settings_lock:
            targets = [c for c in self._clients.values() if c.host_ip == host and c.port == port]
            if action == "remove" and self.management.owner and self.management.owner["address"] == address:
                raise ValueError("不能移除当前管理界面所在从机，请先从其他设备接管管理")
            settings = self.config_store.load()
            def matches(value):
                h, sep, p = value.rpartition(":")
                return (h if sep else value) == host and (int(p) if sep else 9527) == port
            addresses = list(settings.get("slaves", []))
            if action == "add":
                if not any(matches(a) for a in addresses): addresses.append(address)
                self.config_store.update({"slaves": addresses})
                if not targets:
                    from host.connection import SlaveClient
                    client = SlaveClient(host, port)
                    client.set_loop(asyncio.get_running_loop())
                    self.register_client(client)
                    channel = AndroidCurrentChannel(self, client, self.adapter)
                    self._management_channels.append(channel)
                    self._current_tasks.append(asyncio.create_task(channel.run()))
                    await client.manual_connect()
                else:
                    await targets[0].manual_connect()
            else:
                self.config_store.update({"slaves": [a for a in addresses if not matches(a)],
                    "current_slaves": [a for a in settings.get("current_slaves", []) if not matches(a)]})
                for client in targets:
                    await client.stop()
                    task = getattr(client, "_run_task", None)
                    if task and not task.done(): task.cancel()
                    for index in range(len(self._management_channels) - 1, -1, -1):
                        if self._management_channels[index].client is client:
                            self._current_tasks.pop(index).cancel()
                            self._management_channels.pop(index)
                    if client in self._current_clients: self._current_clients.remove(client)
                    self.unregister_client(client.slave_id)
            self.activity.record(source, ("添加从机 " if action == "add" else "移除从机 ") + address, "已保存主机配置")
        return self.slave_list()

    @property
    def llm(self):
        if self._llm is None:
            from cloud_pc.host_program.llm_service import LLMService
            self._llm = LLMService(config_path=self.llm_config)
        return self._llm

    async def llm_request(self, operation, body, source="127.0.0.1"):
        label = "LLM " + str(body.get("provider", "全部")) + " " + operation
        record = self.activity.record(source, label, "进行中") if operation != "current" else None
        try:
            result = await asyncio.to_thread(self.llm.dispatch, operation, body)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)[:200]}
        if record:
            self.activity.record(source, label, "完成" if result.get("ok") else "失败", record)
        return result

    def enable_android_current(self, client):
        if client not in self._current_clients:
            self._current_clients.append(client)

    async def close(self):
        await self.management.close()
        for task in self._current_tasks:
            task.cancel()
        await asyncio.gather(*self._current_tasks, return_exceptions=True)
        self._current_tasks.clear()
        await self.relay.close()
        if self._llm:
            self._llm.close()
            self._llm = None

    def register_client(self, client: "SlaveClient"):
        previous = self._relay_unsubscribe.pop(client.slave_id, None)
        if previous:
            previous()
        self._clients[client.slave_id] = client
        client.activity_callback = lambda action, state: self.activity.record(client.host_ip, action, state)
        self._relay_unsubscribe[client.slave_id] = self.relay.attach(client)

    def unregister_client(self, slave_id: str):
        unsubscribe = self._relay_unsubscribe.pop(slave_id, None)
        if unsubscribe:
            unsubscribe()
        self._clients.pop(slave_id, None)

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    async def request(self, slave_id: str, msg_type: int,
                      payload: dict, expect_type: int,
                      timeout: float = 30.0) -> dict:
        """发送请求到从机，拦截指定响应类型

        msg_type: 发送的消息类型
        expect_type: 期望接收的响应类型
        """
        client = self._clients.get(slave_id)
        if client is None:
            return {"error": f"Slave not found: {slave_id}"}
        if not client.is_connected:
            return {"error": "Slave not connected"}

        # Older reply bodies have no request ID. Serialize identical reply
        # types rather than allowing concurrent API calls to steal a reply.
        lock = self._request_locks.setdefault((slave_id, expect_type), asyncio.Lock())
        async with lock:
            fut = asyncio.get_running_loop().create_future()

            async def receive(p):
                if not fut.done():
                    fut.set_result(p)

            unsubscribe = client.subscribe(expect_type, receive)
            action = {0x0101: "列出目录", 0x0102: "下载文件", 0x0104: "查询文件",
                      0x0105: "删除文件", 0x0107: "创建目录", 0x0108: "更新文件",
                      0x0301: "获取屏幕截图", 0x0401: "转发UE命令", 0x0402: "转发设备请求"}.get(msg_type)
            operation_id = self.activity.record(client.host_ip, action, "进行中") if action else None
            try:
                await client.send(msg_type, payload)
                result = await asyncio.wait_for(fut, timeout=timeout)
                if operation_id:
                    self.activity.record(client.host_ip, action, "失败" if result.get("error") or result.get("ok") is False else "完成", operation_id)
                return result
            except asyncio.TimeoutError:
                if operation_id: self.activity.record(client.host_ip, action, "超时", operation_id)
                return {"error": "Request timeout"}
            except BaseException:
                if operation_id: self.activity.record(client.host_ip, action, "中断", operation_id)
                raise
            finally:
                unsubscribe()

    # ── HTTP 服务器 ───────────────────────────────────

    async def start(self):
        server = await asyncio.start_server(
            self._handle_http, "127.0.0.1", API_PORT
        )
        logger.info(f"API server on http://127.0.0.1:{API_PORT}")
        await self.management.start(self.management_bind, self.management_port)
        self._management_channels = [AndroidCurrentChannel(self, client, self.adapter,
                                    legacy=client in self._current_clients) for client in self._clients.values()]
        self._current_tasks = [asyncio.create_task(channel.run()) for channel in self._management_channels]
        try:
            async with server:
                await server.serve_forever()
        finally:
            await self.close()

    async def _handle_http(self, reader, writer):
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30.0)
            request = raw.decode("ascii")
            lines = request.split("\r\n")
            if not lines:
                return

            method, path, _ = lines[0].split(" ", 2)
            headers = {}
            body_start = 0
            for i, line in enumerate(lines[1:], 1):
                if line == "":
                    body_start = i + 1
                    break
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            if "transfer-encoding" in headers:
                raise ValueError("Transfer-Encoding is unsupported")
            length = int(headers.get("content-length", "0"))
            if not 0 <= length <= 1024 * 1024:
                raise ValueError("Body too large")
            body = (await asyncio.wait_for(reader.readexactly(length), timeout=30.0)).decode("utf-8")

            parsed = urlparse(path)
            qs = parse_qs(parsed.query)

            # 路由
            path_parts = parsed.path.strip("/").split("/")

            session_route = parsed.path in {"/api/management/session/activate", "/api/management/session/release"}
            guarded = (parsed.path.startswith("/api/management/") or
                       parsed.path == "/api/cloud/screen/capture" or "x-management-session" in headers)
            denied = self.management.authorize(headers.get("x-management-session"), "host") if guarded and not session_route else None
            if headers.get("origin") and guarded:
                result = {"ok": False, "error": "Browser-origin management is not allowed"}
            elif session_route and method == "POST":
                data = json.loads(body)
                if not isinstance(data, dict): raise ValueError("JSON body must be an object")
                if parsed.path.endswith("activate"):
                    result = await self.management.activate(data.get("session_id"), "host")
                else:
                    result = self.management.release(data.get("session_id"), "host")
            elif denied:
                result = denied
            elif parsed.path == "/api/management/slaves" and method == "POST":
                data = json.loads(body)
                if not isinstance(data, dict): raise ValueError("JSON body must be an object")
                result = await self.slave_settings(data)
            elif parsed.path.startswith("/api/management/llm/") and method == "POST":
                data = json.loads(body)
                if not isinstance(data, dict): raise ValueError("JSON body must be an object")
                result = await self.llm_request(parsed.path.rsplit("/", 1)[1], data)
            elif parsed.path == "/api/management/current" and method == "GET":
                result = await asyncio.to_thread(self.adapter.snapshot)
            elif parsed.path in {"/api/management/current/send", "/api/management/current/stop"} and method == "POST":
                if headers.get("origin"):
                    result = {"ok": False, "error": "Browser-origin input is not allowed"}
                else:
                    data = json.loads(body)
                    if not isinstance(data, dict):
                        raise ValueError("JSON body must be an object")
                    label = "转发Codex " + parsed.path.rsplit("/", 1)[1].upper()
                    activity_id = self.activity.record("127.0.0.1", label, "进行中")
                    try:
                        result = await asyncio.to_thread(self.adapter.action, parsed.path.rsplit("/", 1)[1], data)
                        self.activity.record("127.0.0.1", label, "完成" if result.get("ok") else "失败", activity_id)
                    except BaseException:
                        self.activity.record("127.0.0.1", label, "中断", activity_id)
                        raise
            elif parsed.path == "/api/cloud/screen/capture" and method == "POST":
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ValueError("JSON body must be an object")
                targets = [c for c in self._clients.values() if c.host_ip == data.get("slave_host")
                           and c.port == int(data.get("slave_port", 9527))]
                if len(targets) != 1:
                    result = {"ok": False, "error": "从机未配置或目标不唯一"}
                else:
                    result = await self.request(targets[0].slave_id, 0x0301,
                        {"width": 640, "height": 360, "quality": 65}, 0x0381, timeout=15)
            elif parsed.path == "/api/management/activity" and method == "GET":
                result = self.activity.snapshot(self._clients.values())
            elif parsed.path == "/health":
                result = {"ok": True, "service": "cloud-pc-host",
                          "app_version": __version__,
                          "management_session": self.management.status(),
                          "current_protocol": "ui-request-v1",
                          "llm_protocol": "session-api-v1",
                          "current_slaves": [client.address for client in self._current_clients]}
                result["management_channels"] = [{"address": channel.client.address,
                    "state": channel.client.state, "task_done": task.done(), **channel.stats}
                    for channel, task in zip(self._management_channels, self._current_tasks)]
            elif path_parts[0] == "api":
                if len(path_parts) == 1 or path_parts[1] == "slaves":
                    result = {"slaves": [
                        {"id": c.slave_id, "name": c.slave_name, "address": c.address,
                         "connected": c.is_connected, "capabilities": sorted(c.capabilities)}
                        for c in self._clients.values()
                    ]}
                elif len(path_parts) >= 2:
                    slave_id = path_parts[1]
                    action = path_parts[2] if len(path_parts) > 2 else ""
                    if action == "connect" and headers.get("origin"):
                        raise ValueError("Browser-origin connection control is not allowed")
                    result = await self._route(method, slave_id, action, qs, body)
                else:
                    result = {"error": "Bad path"}
            else:
                result = {"error": "Not found", "path": parsed.path}

            resp = json.dumps(result, ensure_ascii=False, indent=2)
            writer.write(
                f"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n"
                f"Content-Length: {len(resp.encode('utf-8'))}\r\n"
                f"Cache-Control: no-store\r\nConnection: close\r\n\r\n{resp}".encode("utf-8")
            )
            await writer.drain()
        except Exception as e:
            logger.debug(f"HTTP error: {e}")
            error_body = json.dumps({"ok": False, "error": str(e)[:200]}, ensure_ascii=False).encode("utf-8")
            writer.write((f"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {len(error_body)}\r\nConnection: close\r\n\r\n").encode() + error_body)
            await writer.drain()
        finally:
            writer.close()

    async def _route(self, method: str, slave_id: str, action: str,
                     qs: dict, body: str) -> dict:
        """路由 API 请求"""
        path_val = qs.get("path", [""])[0]

        if action == "connect":
            if method != "POST":
                return {"ok": False, "error": "POST required"}
            client = self._clients.get(slave_id)
            if client is None:
                return {"ok": False, "error": "Slave not configured"}
            return {"id": slave_id, "address": client.address, **await client.manual_connect()}

        if action == "list":
            if method != "GET":
                return {"error": "GET required"}
            client = self._clients.get(slave_id)
            if client and RPC_CAPABILITY in client.capabilities:
                result = await client.rpc("file.list", {"path": path_val or "/"})
                return result.get("result", result)
            return await self.request(
                slave_id, 0x0101, {"path": path_val}, expect_type=0x0181
            )

        elif action == "management_diag":
            if method != "GET":
                return {"error": "GET required"}
            return await self.request(slave_id, 0x0605, {"local_management_probe": True}, 0x0685, timeout=8)

        elif action == "rpc":
            if method != "POST":
                return {"error": "POST required"}
            client = self._clients.get(slave_id)
            if client is None:
                return {"error": "Slave not found"}
            try:
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ValueError("Body must be an object")
                return await client.rpc(data.get("operation"), data.get("payload", {}))
            except (ValueError, RuntimeError, ConnectionError, asyncio.TimeoutError) as exc:
                return {"ok": False, "error": str(exc) or "Request timeout"}

        elif action == "download":
            if method != "GET":
                return {"error": "GET required"}
            return await self._download_file(slave_id, path_val)

        elif action == "exec":
            if method != "POST":
                return {"error": "POST required"}
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return {"error": "Invalid JSON body"}
            cmd = data.get("cmd", "")
            cwd = data.get("cwd", "")
            client = self._clients.get(slave_id)
            if client is None: return {"ok": False, "error": "Slave not configured"}
            executable = cmd.split()[0].replace("\\", "/").rsplit("/", 1)[-1][:60] if cmd.strip() else "(empty)"
            label = "执行命令 " + executable
            operation_id = self.activity.record(client.host_ip, label, "进行中")
            try:
                result = await self._exec_command(slave_id, cmd, cwd)
                state = "超时" if result.get("error") == "Task timeout" else "失败" if result.get("error") or result.get("exit_code") not in (None, 0) else "完成"
                self.activity.record(client.host_ip, label, state, operation_id)
                return result
            except BaseException:
                self.activity.record(client.host_ip, label, "中断", operation_id)
                raise

        elif action == "delete":
            if method != "POST":
                return {"error": "POST required"}
            return await self.request(
                slave_id, 0x0105, {"path": path_val}, expect_type=0x0186
            )

        elif action == "mkdir":
            if method != "POST":
                return {"error": "POST required"}
            return await self.request(
                slave_id, 0x0107, {"path": path_val}, expect_type=0x0186
            )

        elif action == "ue_mcp":
            if method != "POST":
                return {"error": "POST required"}
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return {"error": "Invalid JSON body"}
            request = data.get("request", data)
            if not isinstance(request, dict) or "type" not in request:
                return {"error": "request.type is required"}
            return await self.request(
                slave_id, 0x0401,
                {"request": request, "port": data.get("port", 55701),
                 "timeout": data.get("timeout", 30.0)},
                expect_type=0x0481,
                timeout=float(data.get("timeout", 30.0)) + 5.0,
            )

        else:
            return {"error": f"Unknown action: {action}"}

    async def _exec_command(self, slave_id: str, cmd: str, cwd: str) -> dict:
        """执行命令并收集全部输出"""
        task_id = str(uuid.uuid4())[:8]

        # 注册临时回调收集输出
        outputs = []
        done = asyncio.Event()
        exit_code = None

        client = self._clients.get(slave_id)
        if client is None:
            return {"error": "Slave not found"}

        async def receive_output(payload):
            if payload.get("task_id") == task_id:
                outputs.append(payload)

        async def receive_status(payload):
            nonlocal exit_code
            if payload.get("task_id") == task_id and payload.get("status") != "running":
                exit_code = payload.get("exit_code")
                done.set()

        remove_output = client.subscribe(0x0203, receive_output)
        remove_status = client.subscribe(0x0202, receive_status)
        try:
            await client.send(0x0201, {
                "task_id": task_id,
                "cmd": cmd.split()[0] if " " in cmd else cmd,
                "args": cmd.split()[1:] if " " in cmd else [],
                "cwd": cwd,
            })
            await asyncio.wait_for(done.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            return {"error": "Task timeout", "task_id": task_id}
        finally:
            remove_output()
            remove_status()

        return {
            "task_id": task_id,
            "exit_code": exit_code,
            "stdout": "".join(o["text"] for o in outputs if o["stream"] == "stdout"),
            "stderr": "".join(o["text"] for o in outputs if o["stream"] == "stderr"),
        }

    async def _download_file(self, slave_id: str, path: str) -> dict:
        client = self._clients.get(slave_id)
        if client is None:
            return {"error": "Slave not found"}
        filename = path.replace("\\", "/").rsplit("/", 1)[-1]
        filename = "".join(c for c in filename if c.isprintable())[:100]
        action = "调用 DOWNLOAD 读取文件 " + filename
        operation_id = self.activity.record(client.host_ip, action, "进行中")
        try:
            result = await self._download_file_impl(slave_id, path)
            state = "超时" if result.get("error") == "Download timeout" else "失败" if result.get("error") else "完成"
            self.activity.record(client.host_ip, action, state, operation_id)
            return result
        except BaseException:
            self.activity.record(client.host_ip, action, "中断", operation_id)
            raise

    async def _download_file_impl(self, slave_id: str, path: str) -> dict:
        """下载文件并返回内容"""
        chunks = []
        done = asyncio.Event()

        client = self._clients.get(slave_id)
        if client is None:
            return {"error": "Slave not found"}

        async def receive_chunk(payload):
            if payload.get("path") == path:
                chunks.append(payload)

        async def receive_complete(payload):
            if payload.get("path") == path:
                done.set()

        remove_chunk = client.subscribe(0x0182, receive_chunk)
        remove_complete = client.subscribe(0x0183, receive_complete)
        try:
            await client.send(0x0102, {"path": path})
            await asyncio.wait_for(done.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            return {"error": "Download timeout", "path": path}
        finally:
            remove_chunk()
            remove_complete()

        import base64
        chunks.sort(key=lambda c: c.get("chunk", 0))
        data = b"".join(base64.b64decode(c["data"]) for c in chunks)

        try:
            text = data.decode("utf-8")
            return {"path": path, "size": len(data), "text": text}
        except UnicodeDecodeError:
            return {"path": path, "size": len(data), "base64": base64.b64encode(data).decode("ascii")}
