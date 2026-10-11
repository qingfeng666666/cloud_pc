"""Optional local-only adapter process. Never connects to any Cloud-PC slave."""
import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cloud_pc.host_program.current_session import CurrentSession


class AdapterHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"ok": True, "service": "codex-adapter", "protocol": "ui-request-v1",
                             "read_requests": getattr(self.server, "read_requests", 0)})
        elif self.path == "/current":
            with self.server.current_session.lock:
                self.server.read_requests = getattr(self.server, "read_requests", 0) + 1
            logging.info("ADAPTER_READ requested by local host")
            self.reply(200, self.server.current_session.snapshot())
        else:
            self.reply(404, {"error": "Not found"})

    def do_POST(self):
        if self.headers.get("Origin"):
            self.reply(403, {"error": "Browser-origin input is not allowed"})
            return
        if self.path not in {"/send", "/stop"}:
            self.reply(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192 or self.headers.get("Transfer-Encoding"):
                raise ValueError("Invalid request body length")
            self.connection.settimeout(10)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Body must be an object")
            self.reply(200, self.server.current_session.action(self.path[1:], body))
        except (ValueError, OSError) as exc:
            self.reply(400, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description="独立 Codex 本机适配器")
    parser.add_argument("--port", type=int, default=19627)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), AdapterHandler)
    server.current_session = CurrentSession()
    logging.info("Codex adapter listening on 127.0.0.1:%s; no background text collection", args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
