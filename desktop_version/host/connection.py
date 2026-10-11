"""
主机连接管理 — TCP 连接从机、心跳检测、断线重连
支持同时连接多台从机
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from common.protocol import (
    MessageReader, pack_message,
    TYPE_HANDSHAKE_REQ, TYPE_HANDSHAKE_ACK,
    TYPE_HEARTBEAT, TYPE_DISCONNECT,
    TYPE_ERROR,
    TYPE_RPC_REQUEST, TYPE_RPC_RESULT,
)
from common.rpc import RPC_CAPABILITY, validate_request
from host.config import (
    DEFAULT_SLAVE_PORT, HEARTBEAT_INTERVAL,
    HEARTBEAT_TIMEOUT, RECONNECT_MAX, RECONNECT_DELAYS,
)

logger = logging.getLogger("host")


class SlaveClient:
    """一条到从机的 TCP 连接"""

    def __init__(self, host_ip: str, port: int = DEFAULT_SLAVE_PORT,
                 slave_id: str = ""):
        self.host_ip = host_ip
        self.port = port
        self.slave_id = slave_id or str(uuid.uuid4())[:8]
        self.slave_name: str = ""  # 握手后得知

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._callbacks: dict[int, callable] = {}
        self._listeners: dict[int, list] = {}
        self.capabilities: set[str] = set()
        self._rpc_pending: dict[str, asyncio.Future] = {}
        self._reconnect_count = 0
        self._run_task: asyncio.Task | None = None
        self.activity_callback = lambda action, state: None
        self.management_required = False
        self.management_retry_interval = 5
        self._policy_changed = asyncio.Event()

        # ===== FIX: state machine =====
        self.state = "idle"  # idle / connecting / online / failed
        self._last_ack = time.time()

    @property
    def is_connected(self) -> bool:
        return self._writer is not None

    @property
    def address(self) -> str:
        return f"{self.host_ip}:{self.port}"

    # ── 回调注册 ──────────────────────────────────────

    def on_message(self, msg_type: int):
        def decorator(func):
            self._callbacks[msg_type] = func
            return func
        return decorator

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def subscribe(self, msg_type: int, callback):
        """Observe replies without replacing management-window callbacks."""
        listeners = self._listeners.setdefault(msg_type, [])
        listeners.append(callback)

        def unsubscribe():
            if callback in listeners:
                listeners.remove(callback)
            if not listeners:
                self._listeners.pop(msg_type, None)
        return unsubscribe

    async def dispatch_message(self, msg_type: int, payload: dict):
        if msg_type == TYPE_RPC_RESULT:
            future = self._rpc_pending.get(payload.get("request_id"))
            if future is not None and not future.done():
                future.set_result(payload)
        callbacks = list(self._listeners.get(msg_type, ()))
        primary = self._callbacks.get(msg_type)
        if primary:
            callbacks.append(primary)
        for callback in callbacks:
            try:
                await callback(payload)
            except Exception:
                logger.exception("Callback error")

    async def rpc(self, operation: str, payload: dict, timeout: float = 15.0) -> dict:
        if not self.is_connected:
            raise ConnectionError("Slave not connected")
        if RPC_CAPABILITY not in self.capabilities:
            raise ValueError("Slave does not support correlated RPC")
        if len(self._rpc_pending) >= 16:
            raise RuntimeError("Too many RPC requests")
        request_id = uuid.uuid4().hex
        body = {"request_id": request_id, "operation": operation, "payload": payload}
        validate_request(body)
        future = asyncio.get_running_loop().create_future()
        self._rpc_pending[request_id] = future
        try:
            await self.send(TYPE_RPC_REQUEST, body)
            reply = await asyncio.wait_for(future, timeout)
            return {key: value for key, value in reply.items() if key != "request_id"}
        finally:
            self._rpc_pending.pop(request_id, None)

    def _fail_pending_rpc(self):
        self.capabilities.clear()
        for future in self._rpc_pending.values():
            if not future.done():
                future.set_exception(ConnectionError("Slave disconnected"))

    def schedule_send(self, msg_type: int, payload: dict | None = None):
        if self._loop is None:
            logger.warning(f"[{self.slave_id}] No loop set")
            return
        asyncio.run_coroutine_threadsafe(
            self.send(msg_type, payload), self._loop
        )

    async def send(self, msg_type: int, payload: dict | None = None):
        if self._writer is None:
            return
        self._writer.write(pack_message(msg_type, payload or {}))
        await self._writer.drain()

    # ── 连接 / 断开 ───────────────────────────────────

    async def connect(self) -> bool:
        self.state = "connecting"
        self.activity_callback("连接从机", "连接中")
        logger.info(f"[{self.slave_id}] Connecting to {self.address} ...")

        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host_ip, self.port),
                timeout=5.0,
            )
        except Exception as e:
            logger.warning(f"[{self.slave_id}] Connection failed: {e}")
            self.state = "failed"
            self.activity_callback("连接从机", "失败")
            return False

        try:
            self._writer.write(pack_message(TYPE_HANDSHAKE_REQ, {
                "host_name": "Host",
                "capabilities": ["management_push_v1"],
            }))
            await self._writer.drain()

            msg_reader = MessageReader(self._reader)
            msg_type, payload = await asyncio.wait_for(
                msg_reader.read_one(), timeout=10.0
            )

            if msg_type != TYPE_HANDSHAKE_ACK:
                raise ConnectionError("Bad handshake response")

            self.slave_name = payload.get("slave_name", self.host_ip)
            capabilities = payload.get("capabilities", [])
            self.capabilities = set(capabilities) if isinstance(capabilities, list) and all(
                isinstance(item, str) for item in capabilities) else set()
            self._reconnect_count = 0
            self.state = "online"
            self.activity_callback("连接从机", "已连接")
            self._last_ack = time.time()

            logger.info(f"[{self.slave_id}] Connected OK")
            return True

        except Exception as e:
            logger.warning(f"[{self.slave_id}] Handshake failed: {e}")

            try:
                if self._writer:
                    self._writer.close()
            except:
                pass

            self._reader = None
            self._writer = None
            self.state = "failed"
            self.activity_callback("连接握手", "失败")
            return False

    async def disconnect(self):
        self._fail_pending_rpc()
        try:
            await self.send(TYPE_DISCONNECT, {"reason": "host_shutdown"})
            if self._writer:
                self._writer.close()
                await self._writer.wait_closed()
        except Exception:
            pass
        self._reader = None
        self._writer = None
        self.state = "idle"

    # ── 消息循环 ──────────────────────────────────────

    async def manual_connect(self) -> dict:
        """Start another bounded connection cycle; do not create a parallel reader."""
        if self.state == "online" and self.is_connected:
            return {"ok": True, "started": False, "state": self.state, "message": "Already connected"}
        if self._run_task is not None and not self._run_task.done():
            return {"ok": True, "started": False, "state": self.state, "message": "Connection attempt already running"}
        self._reconnect_count = 0
        self.state = "connecting"
        self._run_task = asyncio.create_task(self.run())
        logger.info("[%s] Manual connection requested: %s", self.slave_id, self.address)
        return {"ok": True, "started": True, "state": self.state, "message": "Connection attempt started"}

    async def run(self):
        task = asyncio.current_task()
        if self._run_task is not None and not self._run_task.done() and self._run_task is not task:
            return
        self._run_task = task
        self._running = True

        if not await self.connect():
            await self._reconnect_loop()

        # FIX: always loop safely
        while self._running:

            if self._writer is None or self._reader is None:
                await self._reconnect_loop()
                continue

            try:
                await self._message_loop()
            except Exception as e:
                logger.warning(f"[{self.slave_id}] loop error: {e}")
                self.state = "failed"
                self.activity_callback("从机连接", "断开")

            self._reader = None
            self._writer = None

            if self._running:
                await self._reconnect_loop()

    async def _reconnect_loop(self):
        attempt = 0
        while self.management_required or attempt < RECONNECT_MAX:
            if not self._running:
                return

            delay = self.management_retry_interval if self.management_required else RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            self._reconnect_count = attempt + 1

            label = "active management; persistent" if self.management_required else f"{self._reconnect_count}/{RECONNECT_MAX}"
            logger.info(f"[{self.slave_id}] Reconnecting ({label}) in {delay}s...")
            self._policy_changed.clear()
            try:
                await asyncio.wait_for(self._policy_changed.wait(),timeout=delay)
                continue
            except asyncio.TimeoutError:
                pass

            if await self.connect():
                return
            attempt += 1

        logger.warning(f"[{self.slave_id}] Reconnect failed")
        self._running = False
        self.state = "failed"
        self.activity_callback("自动重连", "次数已用完，等待手动连接")

    def set_management_required(self, required, interval=5):
        if self.management_required != required:
            self.management_required = required
            self._policy_changed.set()
            self.activity_callback("管理从机重连策略", "持续重连" if required else "有限次重连")
        self.management_retry_interval = interval

    async def _message_loop(self):
        assert self._reader is not None
        msg_reader = MessageReader(self._reader)

        heartbeat_task = asyncio.create_task(self._heartbeat_sender())

        try:
            while self._running:
                try:
                    msg_type, payload = await asyncio.wait_for(
                        msg_reader.read_one(),
                        timeout=HEARTBEAT_INTERVAL + 5
                    )
                except asyncio.TimeoutError:
                    if time.time() - self._last_ack > HEARTBEAT_TIMEOUT:
                        raise ConnectionError("Heartbeat timeout")
                    continue

                if msg_type == TYPE_HEARTBEAT:
                    self._last_ack = time.time()
                    continue

                await self.dispatch_message(msg_type, payload)

        finally:
            heartbeat_task.cancel()
            self._fail_pending_rpc()

    async def _heartbeat_sender(self):
        while self._running and self._writer:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await self.send(TYPE_HEARTBEAT, {"echo": int(time.time())})
            except Exception:
                return

    async def stop(self):
        self._running = False
        self._policy_changed.set()
        await self.disconnect()
