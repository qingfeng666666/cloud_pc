"""Bounded single-result RPC shared by the host and Python slave."""
from __future__ import annotations

import asyncio
import uuid

from common.protocol import TYPE_RELAY_REQUEST

RPC_CAPABILITY = "correlated_rpc_v1"
RELAY_CAPABILITY = "host_relay_v1"
READ_OPERATIONS = frozenset({"file.list", "file.stat"})


def error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def validate_request(body: dict) -> tuple[str, str, dict]:
    if not isinstance(body, dict):
        raise ValueError("request must be an object")
    request_id = body.get("request_id")
    operation = body.get("operation")
    payload = body.get("payload", {})
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise ValueError("request_id must contain 1..128 characters")
    if not isinstance(operation, str) or operation not in READ_OPERATIONS:
        raise ValueError("unsupported operation")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    path = payload.get("path", "/")
    if not isinstance(path, str) or len(path) > 4096:
        raise ValueError("invalid path")
    return request_id, operation, payload


def execute_read(files, body: dict) -> dict:
    """Keep existing FileService path/permission policy authoritative."""
    try:
        request_id, operation, payload = validate_request(body)
        path = payload.get("path", "/")
        if operation == "file.list":
            result = {"path": path, "entries": files.list_dir(path)}
        else:
            result = {"path": path, **files.file_stat(path)}
        reply = {"ok": True, "result": result}
    except PermissionError as exc:
        reply = error("PERMISSION_DENIED", str(exc))
    except FileNotFoundError as exc:
        reply = error("NOT_FOUND", str(exc))
    except ValueError as exc:
        reply = error("BAD_REQUEST", str(exc))
    except Exception:
        reply = error("INTERNAL_ERROR", "Operation failed")
    request_id = body.get("request_id", "") if isinstance(body, dict) else ""
    if not isinstance(request_id, str) or len(request_id) > 128:
        request_id = ""
    return {"request_id": request_id, **reply}


class RelaySession:
    """Slave-originated requests on a connection established by the host.

    The slave's receive loop must deliver TYPE_RELAY_RESULT to resolve().
    No socket or connection to the destination is created here.
    """

    def __init__(self, send):
        self.send = send
        self.pending = {}
        self.closed = False

    async def request(self, target_ip: str, operation: str, payload: dict,
                      timeout: float = 15.0) -> dict:
        if self.closed:
            raise ConnectionError("Host session closed")
        if len(self.pending) >= 16:
            raise RuntimeError("Too many relay requests")
        request_id = uuid.uuid4().hex
        body = {"request_id": request_id, "target_ip": target_ip,
                "operation": operation, "payload": payload}
        validate_request(body)
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send(TYPE_RELAY_REQUEST, body)
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    def resolve(self, body: dict):
        future = self.pending.get(body.get("request_id"))
        if future is not None and not future.done():
            future.set_result(body)

    def close(self):
        self.closed = True
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ConnectionError("Host session closed"))
