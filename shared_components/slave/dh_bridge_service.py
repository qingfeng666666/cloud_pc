"""Call the DigitalHumanBridge TCP server from the UE machine itself."""

from __future__ import annotations

import asyncio
import json


class DigitalHumanPoseStream:
    """One UE-local persistent connection used by one Cloud host connection.

    The Cloud host only supplies pose data.  The destination is intentionally
    fixed to the slave machine's loopback bridge, so a remote host cannot make
    the slave connect to an arbitrary address.
    """

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def open(self) -> None:
        if self.writer and not self.writer.is_closing():
            return
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", 55693, limit=4 * 1024 * 1024),
            timeout=self.timeout,
        )

    async def send_frame(self, frame: dict) -> None:
        if not self.writer or self.writer.is_closing():
            raise ConnectionError("DigitalHuman pose stream is not open")
        self.writer.write(
            (json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        )
        await self.writer.drain()

    async def close(self) -> None:
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        self.reader = None
        self.writer = None


async def _read_json_object(reader: asyncio.StreamReader, timeout: float, max_bytes: int = 4 * 1024 * 1024) -> dict:
    """Read one JSON object without assuming the peer formats it on one line.

    Current DigitalHumanBridge builds use a compact response, but older
    deployed plugin builds may send pretty-printed multi-line JSON.  Reading
    with ``readline`` parsed only ``{\\n`` from such a response and produced
    a misleading JSON error.  ``raw_decode`` identifies a complete object
    regardless of whitespace or line formatting.
    """
    decoder = json.JSONDecoder()
    chunks: list[bytes] = []
    received = 0
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("Timed out waiting for a complete DigitalHumanBridge JSON response")
        chunk = await asyncio.wait_for(reader.read(65536), timeout=remaining)
        if not chunk:
            raise ConnectionError("DigitalHumanBridge closed connection without a response")
        chunks.append(chunk)
        received += len(chunk)
        if received > max_bytes:
            raise ValueError("DigitalHumanBridge JSON response exceeds 4 MiB")
        text = b"".join(chunks).decode("utf-8")
        try:
            value, _end = decoder.raw_decode(text.lstrip())
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            raise ValueError("Invalid bridge response: expected a JSON object")
        return value


async def call_dh_bridge(request: dict, host: str = "127.0.0.1", port: int = 55693,
                         timeout: float = 60.0) -> dict:
    # A full MetaHuman frame contains hundreds of local transforms and exceeds
    # asyncio's 64 KiB default StreamReader line limit.
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, limit=4 * 1024 * 1024), timeout=min(timeout, 10.0)
    )
    try:
        writer.write((json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        await writer.drain()
        return await _read_json_object(reader, timeout)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        writer.close()
        await writer.wait_closed()


async def stream_dh_bridge(frames: list[dict], fps: float, host: str = "127.0.0.1",
                           port: int = 55693, timeout: float = 60.0) -> dict:
    """Send raw SMPL-X frames over the original persistent bridge connection."""
    if not frames:
        return {"ok": False, "error": "No frames supplied"}
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=min(timeout, 10.0)
    )
    try:
        interval = 1.0 / max(float(fps), 1.0)
        loop = asyncio.get_running_loop()
        next_deadline = loop.time()
        for frame in frames:
            writer.write((json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            await writer.drain()
            next_deadline += interval
            await asyncio.sleep(max(0.0, next_deadline - loop.time()))
        return {"ok": True, "frames_sent": len(frames), "fps": fps}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        writer.close()
        await writer.wait_closed()
