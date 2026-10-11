"""
从机配置
"""

import os
import socket

# ── 网络 ────────────────────────────────────────────

LISTEN_PORT = 9527  # 从机 TCP 监听端口（主机主动连接此端口）

# ── 从机标识 ─────────────────────────────────────────

SLAVE_NAME = os.environ.get("CLOUD_SLAVE_NAME", socket.gethostname())

# ── 文件服务 ─────────────────────────────────────────

ROOT_DIR = os.environ.get("CLOUD_ROOT_DIR", os.path.expanduser("~"))

# ── 超时 ────────────────────────────────────────────

HEARTBEAT_TIMEOUT = 30  # 30秒收不到主机心跳则断线
