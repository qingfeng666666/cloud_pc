from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
import threading
from urllib.parse import urlsplit


class HostClient:
    def __init__(self, base_url="http://127.0.0.1:19527", host_ip=None):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.port is None or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("管理界面只允许访问本机主机19527或本机从机19528")
        self.base_url = base_url.rstrip("/")
        self.host_ip = host_ip
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.session_id = uuid.uuid4().hex
        self.registered = False
        self.superseded = False
        self.session_lock = threading.Lock()

    def activate(self, force=False):
        with self.session_lock:
            if force:
                self.session_id = uuid.uuid4().hex
                self.registered = self.superseded = False
            if self.superseded: return {"ok": False, "error": "管理界面已被接替，请手动激活"}
            payload = {"session_id": self.session_id}
            if self.host_ip is not None: payload['host_ip'] = self.host_ip
            result = self._request("/api/management/session/activate", payload)
            self.registered = result.get("ok", False)
            if result.get("code") == "management_inactive": self.superseded = True
            return result

    def release(self):
        if self.registered:
            payload = {"session_id": self.session_id}
            if self.host_ip is not None: payload['host_ip'] = self.host_ip
            return self._request("/api/management/session/release", payload)

    def request(self, path, data=None):
        if not self.registered:
            result = self.activate()
            if not result.get("ok"): return result
        return self._request(path, data)

    def _request(self, path, data=None):
        requested_session = self.session_id
        body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
        request = urllib.request.Request(self.base_url + path, data=body,
                                        headers={"Content-Type": "application/json", "X-Management-Session": requested_session})
        try:
            with self.opener.open(request, timeout=30) as response:
                result = json.loads(response.read(4 * 1024 * 1024))
        except urllib.error.HTTPError as exc:
            try:
                result = json.loads(exc.read(4096))
            except Exception:
                raise RuntimeError(f"主机 HTTP {exc.code}") from exc
            raise RuntimeError(result.get("error", f"主机 HTTP {exc.code}")) from exc
        if not isinstance(result, dict):
            raise RuntimeError("Invalid host response")
        if requested_session == self.session_id and result.get("code") == "management_inactive":
            self.superseded = True
            self.registered = False
        return result

    def current(self):
        return self.request("/api/management/current")

    def activity(self):
        return self.request("/api/management/activity")

    def slaves(self, action="list", **values):
        return self.request("/api/management/slaves", {"action": action, **values})

    def llm(self, operation, provider=None, session_id=None, **values):
        body = dict(values)
        if provider is not None: body["provider"] = provider
        if session_id is not None: body["session_id"] = session_id
        return self.request("/api/management/llm/" + operation, body)

    def send(self, request_id, text):
        return self.request("/api/management/current/send", {"request_id": request_id, "text": text})

    def stop(self, request_id):
        return self.request("/api/management/current/stop", {"request_id": request_id})

    def screenshot(self, host, port=9527):
        return self.request("/api/cloud/screen/capture", {
            "slave_host": host, "slave_port": port, "width": 640, "height": 360, "quality": 65})
