"""
设备发现 — UDP 广播/监听

反向模式：从机广播寻找主机，主机静默监听并单播回复。

从机广播 → 255.255.255.255:19999  "我需要主机"
主机收到 → 单播回复给从机          "我是主机, TCP端口9000"
从机收到 → TCP 连接主机
"""

from __future__ import annotations

import socket
import json


DISCOVERY_PORT = 19999
DISCOVERY_MAGIC = b"CLDC"


def create_broadcast_socket() -> socket.socket:
    """创建 UDP 广播 socket（从机用）"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    return sock


def create_listen_socket(port: int = DISCOVERY_PORT) -> socket.socket:
    """创建 UDP 监听 socket（主机用）"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    return sock


def send_discover(sock: socket.socket, slave_name: str,
                  port: int = DISCOVERY_PORT) -> None:
    """从机发送广播，寻找主机"""
    message = json.dumps({
        "type": "discover",
        "slave_name": slave_name,
    }).encode("utf-8")

    # 拼接 magic + json
    packet = DISCOVERY_MAGIC + message
    sock.sendto(packet, ("255.255.255.255", port))


def send_offer(sock: socket.socket, target_addr: tuple[str, int],
               host_name: str, tcp_port: int) -> None:
    """主机单播回复，告诉从机自己的 TCP 地址"""
    message = json.dumps({
        "type": "offer",
        "host_name": host_name,
        "tcp_port": tcp_port,
    }).encode("utf-8")

    packet = DISCOVERY_MAGIC + message
    sock.sendto(packet, target_addr)


def parse_discovery_packet(data: bytes) -> dict | None:
    """解析收到的发现包，返回 JSON dict 或 None"""
    if not data.startswith(DISCOVERY_MAGIC):
        return None
    try:
        return json.loads(data[len(DISCOVERY_MAGIC):].decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
