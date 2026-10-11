"""Serialized current-terminal access for desktop and Android consumers."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import uuid
from collections import OrderedDict

LOG = logging.getLogger("cloud.host.current")


class CurrentSession:
    def __init__(self, backend=None, clock=time.monotonic):
        self.backend = backend
        self.clock = clock
        self.lock = threading.RLock()
        self.session_id = uuid.uuid4().hex[:12]
        self.records = OrderedDict()
        self.number = 0
        self.text = ""
        self.revision = 0
        self.sampled_at = 0.0
        self.busy = False
        self.stopped = False
        self.idle_since = None
        self.transcript = None

    def _backend(self):
        if self.backend is None:
            from cloud_pc.host_program import codex_adapter
            self.backend = codex_adapter
        return self.backend

    def _observe(self):
        busy = self._backend().codex_is_busy()
        if busy:
            if not self.busy:
                self.stopped = False
            self.busy = True
            self.idle_since = None
        elif self.busy:
            if self.idle_since is None:
                self.idle_since = self.clock()
            elif self.clock() - self.idle_since >= 2:
                self.busy = False
                self.stopped = False
                self.idle_since = None
        return busy

    def snapshot(self):
        with self.lock:
            available, failure = True, ""
            try:
                backend = self._backend()
                value = backend.current_terminal_text()
                if self.transcript is None:
                    self.transcript = backend.Transcript()
                text = self.transcript.update(value)
                if text != self.text:
                    self.text = text
                    self.revision += 1
                self.sampled_at = time.time()
                busy = self._observe()
            except Exception as exc:
                available, failure, busy = False, str(exc)[:200], False
            return {"ok": available, "available": available, "error": failure,
                    "text": self.text, "revision": self.revision,
                    "sampled_at": self.sampled_at, "busy": busy,
                    "stopped": self.stopped,
                    "session_id": self.session_id,
                    "latest": ({k: v for k, v in next(reversed(self.records.values())).items()
                                if k != "fingerprint"} if self.records else None)}

    def action(self, kind: str, data: dict):
        request_id = data.get("request_id", "")
        text = data.get("text", "")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,96}", request_id):
            raise ValueError("Invalid request_id")
        if kind not in {"send", "stop"}:
            raise ValueError("Invalid action")
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if kind == "send" and (not text or len(text.encode("utf-8")) > 1024 or
                               any(ord(c) < 32 or ord(c) == 127 for c in text)):
            raise ValueError("输入须为非空单行，最多 1024 UTF-8 字节")
        fingerprint = hashlib.sha256((kind + "\0" + text).encode()).hexdigest()
        with self.lock:
            previous = self.records.get(request_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ValueError("request_id conflict")
                return {k: v for k, v in previous.items() if k != "fingerprint"}
            self.number += 1
            record = {"request_id": request_id, "number": self.number,
                      "send_id": f"{self.session_id}-{self.number:06d}",
                      "action": kind, "ok": False, "stage": "processing",
                      "fingerprint": fingerprint}
            self.records[request_id] = record
            try:
                backend = self._backend()
                if kind == "send":
                    backend.paste_to_current_window(text)
                    record.update(ok=True, message="已输入文字并发送一次 Enter", stage="submitted")
                elif not self._observe():
                    record.update(message="Codex 当前空闲，未发送 ESC", stage="ignored")
                elif self.stopped:
                    record.update(message="本轮已发送 ESC，未重复发送", stage="ignored")
                else:
                    backend.escape_current_window()
                    self.stopped = True
                    record.update(ok=True, message="已向工作中的 Codex 发送一次 ESC", stage="stopped")
            except Exception as exc:
                record.update(message=str(exc)[:200], stage="failed")
            LOG.info("CURRENT_%s id=%s number=%s stage=%s bytes=%s message=%s", kind.upper(),
                     request_id, self.number, record["stage"], len(text.encode("utf-8")), record.get("message", ""))
            while len(self.records) > 128:
                self.records.popitem(last=False)
            return {k: v for k, v in record.items() if k != "fingerprint"}
