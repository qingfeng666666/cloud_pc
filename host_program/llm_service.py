"""Host-owned LLM routing. Selection is explicit per request, never global."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from cloud_pc.host_program.llm_backends import BACKENDS, run_status

LOG = logging.getLogger("cloud.host.llm")
OPERATIONS = {"providers", "sessions", "create", "open", "current", "send", "stop"}


class LLMService:
    def __init__(self, config_path=None, backends=None, state_path=None):
        default = Path.home() / ".config/cloud_pc/llm.json"
        self.config_path = Path(config_path or os.environ.get("CLOUD_LLM_CONFIG") or default)
        fallback = Path(__file__).with_name("llm_config.json")
        if config_path or os.environ.get("CLOUD_LLM_CONFIG") or self.config_path.exists():
            self.config = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        else:
            self.config = json.loads(fallback.read_text(encoding="utf-8"))
        if 'llm' in self.config: self.config = self.config['llm']
        self.backends = backends if backends is not None else {}
        self.lock = threading.RLock()
        self.session_locks = {}
        self.stopped = set()
        self.working_since = {}
        self.queued = {}
        self.queue_exit = threading.Event()
        self.queue_thread = None
        self.next_send_after = {}
        self.queue_notices = {}
        state = state_path or Path.home() / ".local/state/cloud_pc/llm_requests.sqlite3"
        if str(state) != ":memory:":
            Path(state).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(state), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS requests (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, fingerprint TEXT, result TEXT)")
        self.db.commit()
        # Queued text is memory-only. Never replay it after an uncertain shutdown.
        for rid, raw in self.db.execute("SELECT id,result FROM requests WHERE result IS NOT NULL").fetchall():
            saved = json.loads(raw)
            if saved.get('stage') == 'queued':
                saved.update(ok=False, stage='unknown', message='主机已重启，排队内容未发送，请核实后重新提交')
                self.db.execute("UPDATE requests SET result=? WHERE id=?", (json.dumps(saved, ensure_ascii=False), rid))
        self.db.commit()
        if str(state) != ":memory:" and os.name != "nt": Path(state).chmod(0o600)

    def backend(self, provider):
        if provider not in BACKENDS: raise ValueError("不支持的 LLM")
        with self.lock:
            if provider not in self.backends:
                config = self.config.get(provider, {})
                if not config.get("enabled", False): raise ValueError("该 LLM 未在主机启用")
                self.backends[provider] = BACKENDS[provider](config)
            return self.backends[provider]

    def dispatch(self, operation, body):
        try:
            return self._dispatch(operation, body)
        except Exception as exc:
            LOG.warning("LLM operation=%s provider=%s failed=%s", operation,
                        body.get("provider") if isinstance(body, dict) else None, type(exc).__name__)
            state = "disconnected" if isinstance(exc, (OSError, TimeoutError)) or any(
                word in str(exc) for word in ("连接断开", "连接已关闭", "超时")) else "failed"
            return {"ok": False, "error": str(exc)[:200], "run_status": run_status(state),
                    "status_text": run_status(state)["label"]}

    def _dispatch(self, operation, body):
        if operation not in OPERATIONS or not isinstance(body, dict): raise ValueError("无效 LLM 操作")
        if operation == "providers":
            return {"ok": True, "providers": [{"id": p, "name": p,
                    "enabled": self.config.get(p, {}).get("enabled", False)} for p in BACKENDS]}
        provider, sid = body.get("provider"), body.get("session_id")
        backend = self.backend(provider)
        if operation == "sessions":
            cursor = body.get("cursor")
            if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 2048): raise ValueError("Invalid cursor")
            rows, following = backend.sessions(cursor)
            return {"ok": True, "provider": provider, "sessions": rows, "next_cursor": following}
        if operation != "create" and (not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", sid)):
            raise ValueError("请选择有效会话")
        with self.lock:
            target_lock = self.session_locks.setdefault((provider, sid), threading.RLock())
        with target_lock:
            if operation in {"open", "current"}:
                result = getattr(backend, operation)(sid)
                key = (provider, sid)
                if result.get("busy"):
                    start = self.working_since.setdefault(key, time.monotonic())
                    result["working_seconds"] = max(0, int(time.monotonic() - start))
                else:
                    self.working_since.pop(key, None)
                    result["working_seconds"] = 0
                if not result.get("busy"): self.stopped.discard((provider, sid))
                state = result.setdefault("run_status", run_status("working" if result.get("busy") else "idle"))
                label = state.get("label", "Working" if result.get("busy") else "空闲")
                if state.get("state") == "retrying" and state.get("attempt") is not None:
                    label += " · 第" + str(state["attempt"]) + "次"
                result["status_text"] = (f"{label}（{result['working_seconds']}s）"
                                         if result.get("busy") else label)
                result['queued_count'] = len(self.queued.get(key, []))
                if result['queued_count']: result['status_text'] += f" · 下一轮待发送 {result['queued_count']} 条"
                notice = self.queue_notices.get(key)
                if notice:
                    result['queue_notice'] = dict(notice)
                    result['status_text'] += f" · #{notice['number']} {notice['message']}"
                result.update(provider=provider, session_id=sid, stopped=(provider, sid) in self.stopped)
                return result
            # Durable request IDs prevent a duplicate SEND/create after a host restart.
            rid = body.get("request_id")
            if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,96}", rid): raise ValueError("Invalid request_id")
            text = body.get("text", "")
            if operation == "send" and (not isinstance(text, str) or not text.strip() or
                    len(text.encode()) > 16384 or "\0" in text): raise ValueError("输入不能为空，最多16KB")
            fingerprint = hashlib.sha256(json.dumps([operation, provider, sid, text], ensure_ascii=False).encode()).hexdigest()
            with self.lock:
                previous = self.db.execute("SELECT number, fingerprint, result FROM requests WHERE id=?", (rid,)).fetchone()
                if previous:
                    if previous[1] != fingerprint: raise ValueError("request_id conflict")
                    return json.loads(previous[2]) if previous[2] else {"ok": False, "number": previous[0],
                        "error": "此前请求执行结果未确认；为避免重复操作未重发", "stage": "unknown"}
                number = self.db.execute("INSERT INTO requests(id,fingerprint) VALUES(?,?)", (rid, fingerprint)).lastrowid
                self.db.commit()
            result = {"ok": False, "number": number, "request_id": rid, "provider": provider,
                      "session_id": sid, "stage": "failed"}
            try:
                if operation == "create":
                    result.update(session_id=backend.create(), ok=True, message="已新建会话", stage="created")
                else:
                    status = backend.current(sid)
                    if operation == "send":
                        if status.get("busy") or self.queued.get((provider, sid)):
                            with self.lock:
                                if sum(len(q) for q in self.queued.values()) >= 64: raise ValueError('排队消息已满')
                                result.update(ok=True, message="已排队，当前轮结束后发送", stage="queued")
                                self.queued.setdefault((provider, sid), []).append((rid, text, result))
                            with self.lock:
                                if self.queue_thread is None:
                                    self.queue_thread = threading.Thread(target=self.queue_worker, daemon=True, name='llm-next-turn')
                                    self.queue_thread.start()
                        else:
                            backend.send(sid, text)
                            result.update(ok=True, message="已通过 API 提交一次，等待回复", stage="submitted")
                            self.working_since[(provider, sid)] = time.monotonic()
                            self.next_send_after[(provider, sid)] = time.monotonic() + 2
                        self.stopped.discard((provider, sid))
                    elif not status.get("busy") or (provider, sid) in self.stopped:
                        result.update(ok=True, message="当前空闲或本轮已停止，未重复中止", stage="ignored")
                    else:
                        backend.stop(sid)
                        self.stopped.add((provider, sid))
                        for queued_id, _, saved in self.queued.pop((provider, sid), []):
                            saved.update(ok=False, stage='cancelled', message='停止操作已取消尚未发送的排队消息')
                            with self.lock:
                                self.db.execute('UPDATE requests SET result=? WHERE id=?', (json.dumps(saved, ensure_ascii=False), queued_id))
                        result.update(ok=True, message="已请求停止当前回合", stage="stopped")
            except Exception as exc:
                result.update(error=str(exc)[:200], message=str(exc)[:200])
            with self.lock:
                self.db.execute("UPDATE requests SET result=? WHERE id=?", (json.dumps(result, ensure_ascii=False), rid))
                self.db.commit()
            LOG.info("LLM_%s provider=%s session=%s id=%s number=%s stage=%s bytes=%s", operation.upper(),
                     provider, result.get("session_id"), rid, number, result["stage"], len(text.encode()) if isinstance(text,str) else 0)
            return result

    def queue_worker(self):
        while not self.queue_exit.wait(1):
            self.process_queued()

    def process_queued(self):
        with self.lock: keys = list(self.queued)
        for key in keys:
            with self.lock: target_lock = self.session_locks.setdefault(key, threading.RLock())
            with target_lock:
                queue = self.queued.get(key)
                if not queue: continue
                if time.monotonic() < self.next_send_after.get(key, 0): continue
                try:
                    backend = self.backend(key[0])
                    status = backend.current(key[1])
                    if status.get('busy'): continue
                except Exception:
                    continue  # Retry only status reads; never replay a send.
                rid, text, saved = queue.pop(0)
                if not queue:
                    with self.lock: self.queued.pop(key, None)
                try:
                    backend.send(key[1], text)
                    self.working_since[key] = time.monotonic()
                    self.next_send_after[key] = time.monotonic() + 2
                    saved.update(ok=True, stage='submitted', message='排队消息已在下一轮提交一次')
                except Exception:
                    saved.update(ok=False, stage='unknown', message='排队消息提交结果未确认，未重发')
                    # Do not send subsequent messages into an uncertain turn.
                    for next_id, _, next_saved in self.queued.pop(key, []):
                        next_saved.update(ok=False, stage='cancelled', message='前一条提交未确认，已取消排队')
                        with self.lock:
                            self.db.execute('UPDATE requests SET result=? WHERE id=?', (json.dumps(next_saved, ensure_ascii=False), next_id))
                with self.lock:
                    self.db.execute('UPDATE requests SET result=? WHERE id=?', (json.dumps(saved, ensure_ascii=False), rid))
                    self.db.commit()
                self.queue_notices[key] = {'number': saved['number'], 'stage': saved['stage'],
                    'message': saved['message']}
                LOG.info('LLM_QUEUE provider=%s session=%s id=%s stage=%s', *key, rid, saved['stage'])

    def close(self):
        self.queue_exit.set()
        if self.queue_thread: self.queue_thread.join()
        for backend in self.backends.values(): backend.close()
        self.db.close()
