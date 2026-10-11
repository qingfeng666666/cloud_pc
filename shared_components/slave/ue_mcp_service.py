"""从机本机 UnrealMCP 调用服务。

UE MCP 通常只监听 127.0.0.1，因此必须由运行 UE 的从机发起连接。
"""

from __future__ import annotations

import asyncio
import json


async def call_ue_mcp(request: dict, host: str = "127.0.0.1", port: int = 55701,
                      timeout: float = 30.0) -> dict:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=min(timeout, 10.0)
    )
    try:
        writer.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(4 * 1024 * 1024), timeout=timeout)
        if not raw:
            return {"ok": False, "error": "UE MCP closed connection without a response"}
        text = raw.decode("utf-8", errors="replace").strip()
        try:
            return {"ok": True, "response": json.loads(text)}
        except json.JSONDecodeError:
            return {"ok": True, "raw": text}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        writer.close()
        await writer.wait_closed()
