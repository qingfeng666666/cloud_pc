"""Explicitly granted application-message routing; never scans or dials unknown IPs."""
from __future__ import annotations

import asyncio
import ipaddress
import logging

from common.protocol import TYPE_RELAY_REQUEST, TYPE_RELAY_RESULT
from common.rpc import RPC_CAPABILITY, error, validate_request

logger = logging.getLogger("host.relay")


class RelayRouter:
    def __init__(self, clients: dict, grants=()):
        self.clients = clients
        self.grants = set(grants)
        self.tasks = set()
        self.pending = set()

    def attach(self, source):
        async def receive(body):
            # Never await a destination response in a source receive loop:
            # reciprocal requests otherwise deadlock the two readers.
            request_id = body.get("request_id", "") if isinstance(body, dict) else ""
            if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
                await source.send(TYPE_RELAY_RESULT, {"request_id": "", **error("BAD_REQUEST", "Invalid request_id")})
                return
            key = (source.slave_id, request_id)
            if key in self.pending or len(self.tasks) >= 16:
                await source.send(TYPE_RELAY_RESULT, {"request_id": request_id, **error("BUSY", "Relay busy or duplicate request")})
                return
            self.pending.add(key)
            task = asyncio.create_task(self._route(source, body, source._writer))
            self.tasks.add(task)
            def finished(done):
                self.tasks.discard(done)
                self.pending.discard(key)
                if not done.cancelled() and done.exception():
                    logger.error("Relay transport failed: %s", done.exception())
            task.add_done_callback(finished)
        return source.subscribe(TYPE_RELAY_REQUEST, receive)

    async def _route(self, source, body, source_writer):
        request_id = body["request_id"]
        try:
            _, operation, payload = validate_request(body)
            target_ip = str(ipaddress.ip_address(body.get("target_ip", "")))
            source_ip = str(ipaddress.ip_address(source.host_ip))
            if (source_ip, target_ip, operation) not in self.grants:
                result = error("FORBIDDEN", "No explicit source/target/operation grant")
            else:
                targets = [c for c in self.clients.values() if c.host_ip == target_ip]
                if not targets:
                    result = error("UNKNOWN_TARGET", "Target is not configured on host")
                elif len(targets) != 1:
                    result = error("AMBIGUOUS_TARGET", "Multiple configured endpoints have this IP")
                elif targets[0] is source:
                    result = error("LOOP_REJECTED", "Cannot relay to source")
                elif not targets[0].is_connected:
                    result = error("OFFLINE", "Target is offline")
                elif RPC_CAPABILITY not in targets[0].capabilities:
                    result = error("UNSUPPORTED", "Target needs correlated RPC support")
                else:
                    result = await targets[0].rpc(operation, payload)
        except ValueError as exc:
            result = error("BAD_REQUEST", str(exc))
        except asyncio.TimeoutError:
            result = error("TIMEOUT", "Target response timed out")
        except ConnectionError:
            result = error("DISCONNECTED", "Target disconnected")
        except RuntimeError as exc:
            result = error("BUSY", str(exc))
        except Exception:
            logger.exception("Relay failed source=%s request=%s", source.slave_id, request_id)
            result = error("INTERNAL_ERROR", "Relay failed")
        logger.info("relay source=%s request=%s target=%s ok=%s", source.slave_id,
                    request_id, body.get("target_ip"), result.get("ok"))
        # Never deliver an old session's result to a reconnected session.
        if source_writer is not None and source._writer is source_writer:
            await source.send(TYPE_RELAY_RESULT, {"request_id": request_id, **result})

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
