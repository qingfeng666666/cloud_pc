"""Cloud host's Android current-window transport on its existing CLDC session."""
from __future__ import annotations

import asyncio
import logging
import re
import time

from common.protocol import TYPE_MANAGEMENT_REQUEST

LOG = logging.getLogger("cloud.host.android_current")


class AndroidCurrentChannel:
    def __init__(self, api, client, current, legacy=True):
        self.api, self.client, self.current = api, client, current
        self.legacy = legacy
        self.last_error = None
        self.last_revision = None
        self._tasks = set()
        self._seen = set()
        self._recent = []
        self._mutations = asyncio.Lock()
        self._background_reads = asyncio.Semaphore(2)
        self._foreground_reads = asyncio.Semaphore(2)
        self._view_epoch = 0
        self.stats = {"management_requests": 0}

    async def request(self, kind, body, reply):
        result = await self.api.request(self.client.slave_id, kind, body, reply, timeout=10)
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "Android request failed"))
        return result

    async def run(self):
        # Callback only schedules work: never block the CLDC reader, which
        # must remain able to receive heartbeats and result acknowledgements.
        unsubscribe = self.client.subscribe(TYPE_MANAGEMENT_REQUEST, self.receive_request)
        try:
            await asyncio.Future()  # Wait for pushed frames; no business polling.
        finally:
            unsubscribe()
            tasks = list(self._tasks)
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def receive_request(self, event):
        if not isinstance(event, dict): return
        request_id = event.get("send_id", "")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id): return
        if request_id in self._seen: return
        if len(self._tasks) >= 16:
            LOG.warning("Management push capacity reached slave=%s", self.client.slave_id)
            # Never execute a request twice or silently queue unbounded work.
            task = asyncio.create_task(self.request(0x0606, {"request_id": request_id,
                "result": {"ok": False, "error": "主机管理请求繁忙；未自动重发"}}, 0x0686))
            self._tasks.add(task)
            task.add_done_callback(self._finished)
            return
        self._seen.add(request_id)
        self._recent.append(request_id)
        if len(self._recent) > 256:
            self._seen.discard(self._recent.pop(0))
        operation = event.get("operation")
        if operation in {"llm_providers", "llm_sessions", "llm_open"}:
            self._view_epoch += 1
        task = asyncio.create_task(self._scheduled(event, self._view_epoch))
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task):
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            LOG.warning("Management push failed slave=%s error=%s", self.client.slave_id,
                        type(task.exception()).__name__)

    async def _scheduled(self, event, epoch):
        operation = event.get("operation")
        background = operation in {"llm_current", "current", "screenshot", "activity"}
        readonly = background or operation in {"llm_providers", "llm_sessions", "llm_open"}
        # 'slaves' also supports add/remove: conservatively keep it on the
        # ordered mutation lane, along with SEND/STOP/create.
        gate = self._background_reads if background else self._foreground_reads if readonly else self._mutations
        started = time.monotonic()
        async with gate:
            if background and epoch != self._view_epoch:
                await self.request(0x0606, {"request_id": event["send_id"], "result":
                    {"ok": False, "code": "superseded", "error": "读取请求已过期"}}, 0x0686)
                return
            await self.management(event)
        LOG.info("Management push operation=%s slave=%s id=%s elapsed_ms=%d", operation,
                 self.client.slave_id, event["send_id"], (time.monotonic() - started) * 1000)

    async def management(self, event):
        self.stats["management_requests"] += 1
        request_id = event.get("send_id", "")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise ValueError("Invalid management request ID")
        operation, body = event.get("operation"), event.get("payload", {})
        activity_id = None
        try:
            if not isinstance(body, dict):
                raise ValueError("Invalid management body")
            body = dict(body)
            denied = self.api.management.authorize(body.pop("_management_session", None), self.client.address)
            if denied:
                result = denied
            elif isinstance(operation, str) and operation.startswith("llm_"):
                result = await self.api.llm_request(operation[4:], body, self.client.host_ip)
            elif operation == "activity":
                result = self.api.activity.snapshot(self.api._clients.values())
            elif operation == "current":
                result = await asyncio.to_thread(self.current.snapshot)
            elif operation in {"send", "stop"}:
                activity_id = self.api.activity.record(self.client.host_ip, "转发Codex " + operation.upper(), "进行中")
                # Keep the UI's ID stable, independent of host reconnect IDs.
                result = await asyncio.to_thread(self.current.action, operation, body)
            elif operation == "slaves":
                result = await self.api.slave_settings(body, self.client.host_ip)
            elif operation == "screenshot":
                targets = [c for c in self.api._clients.values() if c.host_ip == body.get("slave_host")
                           and c.port == int(body.get("slave_port", 9527))]
                if len(targets) != 1:
                    raise ValueError("从机未配置或目标不唯一")
                result = await self.api.request(targets[0].slave_id, 0x0301,
                    {"width": 640, "height": 360, "quality": 65}, 0x0381, timeout=15)
            else:
                raise ValueError("不支持的管理操作")
        except Exception as exc:
            result = {"ok": False, "error": str(exc)[:160]}
        if activity_id:
            self.api.activity.record(self.client.host_ip, "转发Codex " + operation.upper(),
                                     "完成" if result.get("ok") else "失败", activity_id)
        LOG.info("Management operation=%s slave=%s id=%s ok=%s", operation, self.client.slave_id,
                 request_id, result.get("ok", False))
        await self.request(0x0606, {"request_id": request_id, "result": result}, 0x0686)
