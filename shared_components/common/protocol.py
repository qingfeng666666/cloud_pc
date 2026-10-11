"""
云电脑通信协议 — 消息打包/解包

消息格式:
┌──────────┬──────────┬──────────┬──────────┬───────────────┐
│  Magic   │ Version  │   Type   │  Length  │    Payload    │
│  4 bytes │ 2 bytes  │  2 bytes │ 4 bytes  │   N bytes     │
│ "CLDC"   │  0x0001  │          │ 大端序    │  JSON (UTF-8) │
└──────────┴──────────┴──────────┴──────────┴───────────────┘
"""

from __future__ import annotations

import struct
import json
from typing import Any

# ── 常量 ──────────────────────────────────────────────

MAGIC = b"CLDC"
VERSION = 0x0001
HEADER_SIZE = 12  # 4 + 2 + 2 + 4

# ── 消息类型 — 控制类 ──────────────────────────────────

TYPE_HANDSHAKE_REQ = 0x0001  # 握手请求 (主机 → 从机)
TYPE_HANDSHAKE_ACK = 0x0002  # 握手确认 (从机 → 主机)
TYPE_HEARTBEAT = 0x0003      # 心跳
TYPE_DISCONNECT = 0x00FF     # 断开

# ── 消息类型 — 文件类 ──────────────────────────────────

TYPE_LIST_DIR = 0x0101       # 列出目录
TYPE_DOWNLOAD = 0x0102       # 文件下载请求
TYPE_UPLOAD = 0x0103         # 文件上传分块
TYPE_FILE_STAT = 0x0104      # 文件信息
TYPE_DELETE = 0x0105         # 删除
TYPE_RENAME = 0x0106         # 重命名
TYPE_MKDIR = 0x0107          # 创建目录
TYPE_FILE_MUTATE = 0x0108    # 受策略限制的文件写入/创建/删除/重命名

TYPE_LIST_DIR_RESULT = 0x0181  # 目录列表结果 (从机 → 主机)
TYPE_DOWNLOAD_CHUNK = 0x0182   # 下载分块 (从机 → 主机)
TYPE_DOWNLOAD_COMPLETE = 0x0183  # 下载完成 (从机 → 主机)
TYPE_UPLOAD_ACK = 0x0184      # 上传确认 (从机 → 主机)
TYPE_FILE_STAT_RESULT = 0x0185  # 文件信息结果
TYPE_OPERATION_ACK = 0x0186   # 通用操作确认 (删除/重命名/mkdir)
TYPE_FILE_MUTATE_RESULT = 0x0187  # 受策略限制的文件变更结果

# ── 运行时快照推送 ───────────────────────────────────────

# 由主机显式配置。开启后，从机只在截图/有效语音内容变更时将快照推送到
# 主机指定的回调地址；不会扫描、上传其他文件。
TYPE_RUNTIME_SNAPSHOT_CONFIG = 0x0302
TYPE_RUNTIME_SNAPSHOT_CONFIG_RESULT = 0x0382

# ── OpenXR Spatial UI ───────────────────────────────────
# Reliable command/event transport for the AR display runtime. Payloads are
# UTF-8 JSON inside normal CLDC frames.
TYPE_AR_UI_COMMAND = 0x0501
TYPE_AR_UI_COMMAND_RESULT = 0x0581
TYPE_AR_UI_EVENTS_POLL = 0x0502   # 主机来取 AR 点击事件（从机转发到 AR 应用的本机回环）
TYPE_AR_UI_EVENT = 0x0582         # AR 应用的事件回包（{"ok":true,"events":[...]}）

# ── Android 当前窗口（主机 → Android 从机） ────────────────
# 0x0601 / 0x0681 为 0.2.0 截图试验保留，不再发送。
TYPE_ANDROID_CURRENT_EVENTS_POLL = 0x0602 # 主机读取 Android SEND 事件
TYPE_ANDROID_CURRENT_STATUS = 0x0603      # 主机返回 SEND 执行状态
TYPE_ANDROID_CURRENT_TEXT = 0x0604        # 当前 Codex 终端可见文本
TYPE_ANDROID_CURRENT_DIAGNOSTICS = 0x0605 # 只读 SEND 阶段日志，无输入内容
TYPE_ANDROID_CURRENT_EVENTS = 0x0682
TYPE_ANDROID_CURRENT_STATUS_RESULT = 0x0683
TYPE_ANDROID_CURRENT_TEXT_RESULT = 0x0684
TYPE_ANDROID_CURRENT_DIAGNOSTICS_RESULT = 0x0685
TYPE_MANAGEMENT_RESULT = 0x0606           # 主机沿既有连接交付本机管理请求结果
TYPE_MANAGEMENT_RESULT_ACK = 0x0686
TYPE_MANAGEMENT_REQUEST = 0x0607          # 从机沿主机已建立的连接主动提交管理请求

# ── 消息类型 — 任务类 ──────────────────────────────────

TYPE_EXEC = 0x0201           # 执行命令 (主机 → 从机)
TYPE_TASK_STATUS = 0x0202    # 任务状态 (从机 → 主机)
TYPE_TASK_OUTPUT = 0x0203    # 任务输出 (从机 → 主机)
TYPE_TASK_KILL = 0x0204      # 终止任务 (主机 → 从机)

# ── UE MCP 类 ─────────────────────────────────────────

TYPE_UE_MCP_CALL = 0x0401    # 从机本机调用 UnrealMCP (主机 → 从机)
TYPE_DH_BRIDGE_CALL = 0x0402 # 从机本机调用 DigitalHumanBridge TCP (主机 → 从机)
TYPE_DH_STREAM_OPEN = 0x0403  # 打开持续 DigitalHumanBridge 姿势流
TYPE_DH_STREAM_FRAME = 0x0404 # 向持续姿势流写入一帧
TYPE_DH_STREAM_CLOSE = 0x0405 # 关闭持续姿势流
TYPE_DH_AUDIO_PLAY = 0x0406   # 播放语音 (主机 → 从机)：audio(base64 WAV) + delay_ms
TYPE_DH_PERFORMANCE_PACKAGE = 0x0407  # 一句数字人表演包：wav + face_frames + body_package
TYPE_UE_MCP_RESULT = 0x0481  # UnrealMCP 返回结果 (从机 → 主机)
TYPE_DH_BRIDGE_RESULT = 0x0482 # DigitalHumanBridge 返回结果 (从机 → 主机)
TYPE_DH_STREAM_RESULT = 0x0483 # 持续姿势流打开/关闭结果 (从机 → 主机)
TYPE_DH_AUDIO_RESULT = 0x0486 # 语音播放结果 (从机 → 主机)
TYPE_DH_PERFORMANCE_RESULT = 0x0487 # 表演包接收结果 (从机 → 主机)

# ── 消息类型 — 错误 ────────────────────────────────────

TYPE_ERROR = 0x0FFF          # 错误响应

# Correlated, single-result operations and host-mediated slave relay.
# These are separate from legacy replies lacking a request ID.
TYPE_RPC_REQUEST = 0x0701
TYPE_RELAY_REQUEST = 0x0702
TYPE_RPC_RESULT = 0x0781
TYPE_RELAY_RESULT = 0x0782

# ── 打包 / 解包 ────────────────────────────────────────

def pack_message(msg_type: int, payload: dict[str, Any] | None = None) -> bytes:
    """将消息类型和 payload 打包为二进制消息"""
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    header = struct.pack(">4sHHI", MAGIC, VERSION, msg_type, len(body))
    return header + body


def unpack_header(data: bytes) -> tuple[int, int, int]:
    """从 12 字节头部解析 (version, msg_type, payload_length)"""
    magic, version, msg_type, length = struct.unpack(">4sHHI", data[:HEADER_SIZE])
    if magic != MAGIC:
        raise ValueError(f"Invalid magic: {magic!r}, expected {MAGIC!r}")
    return version, msg_type, length


def unpack_message(data: bytes) -> tuple[int, dict[str, Any]]:
    """从完整消息解析 (msg_type, payload_dict)"""
    version, msg_type, length = unpack_header(data)
    payload_bytes = data[HEADER_SIZE:HEADER_SIZE + length]
    payload = json.loads(payload_bytes.decode("utf-8")) if payload_bytes else {}
    return msg_type, payload


# ── 流式读取 ───────────────────────────────────────────

class MessageReader:
    """从 asyncio StreamReader 中逐条读取完整消息"""

    def __init__(self, reader: "asyncio.StreamReader"):
        self._reader = reader
        self._buffer = b""

    async def read_one(self) -> tuple[int, dict[str, Any]]:
        """读取一条完整消息，返回 (msg_type, payload_dict)"""
        # 先读够 12 字节头部
        while len(self._buffer) < HEADER_SIZE:
            chunk = await self._reader.read(4096)
            if not chunk:
                raise ConnectionError("Connection closed")
            self._buffer += chunk

        version, msg_type, length = unpack_header(self._buffer[:HEADER_SIZE])
        total_needed = HEADER_SIZE + length

        # 读够完整 payload
        while len(self._buffer) < total_needed:
            chunk = await self._reader.read(4096)
            if not chunk:
                raise ConnectionError("Connection closed")
            self._buffer += chunk

        # 取出完整消息
        message_bytes = self._buffer[:total_needed]
        self._buffer = self._buffer[total_needed:]

        return unpack_message(message_bytes)
