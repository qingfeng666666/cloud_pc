"""
主机配置
"""

import os

# ── 网络 ────────────────────────────────────────────

DEFAULT_SLAVE_PORT = 9527   # 从机默认 TCP 端口

# ── 心跳 ────────────────────────────────────────────

HEARTBEAT_INTERVAL = 10     # 主机每 10 秒发一次心跳
HEARTBEAT_TIMEOUT = 30      # 30 秒收不到回复判定断线

# ── 重连 ────────────────────────────────────────────

RECONNECT_MAX = 3           # 最多重连 3 次
RECONNECT_DELAYS = [3, 6, 9]  # 每次重连间隔（秒）

# ── 下载目录 ─────────────────────────────────────────

DOWNLOAD_DIR = os.environ.get(
    "CLOUD_DOWNLOAD_DIR",
    os.path.join(os.path.expanduser("~"), "Downloads", "CloudPC"),
)
