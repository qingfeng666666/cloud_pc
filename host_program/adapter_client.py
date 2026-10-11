"""Host routing to an optional, independent localhost Codex adapter."""
import json
import urllib.error
import urllib.request


class CodexAdapterClient:
    def __init__(self, url="http://127.0.0.1:19627"):
        self.url = url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, data=None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
        request = urllib.request.Request(self.url + path, data=body,
                                        headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=20) as response:
                return json.loads(response.read(1024 * 1024))
        except urllib.error.HTTPError as exc:
            raise RuntimeError("Codex 适配器拒绝请求") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Codex 适配器未运行、不可达或超时；未自动重试") from exc

    def snapshot(self):
        try:
            return self.request("/current")
        except RuntimeError as exc:
            return {"ok": False, "available": False, "error": str(exc)}

    def action(self, kind, body):
        if kind not in {"send", "stop"}:
            raise ValueError("Unknown adapter operation")
        return self.request("/" + kind, body)
