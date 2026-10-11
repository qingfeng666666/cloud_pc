"""
从机防火墙 — 自动开放监听端口

Windows: 通过 netsh 添加入站规则（需要管理员权限）
非 Windows: 提示用户手动配置
"""

import logging
import platform
import subprocess
import sys

logger = logging.getLogger("slave.firewall")

RULE_NAME = "Cloud PC Slave"


def open_port(port: int) -> bool:
    """尝试开放 TCP 端口，返回是否成功"""
    if platform.system() == "Windows":
        return _open_port_windows(port)
    else:
        logger.info(f"Please ensure port {port} is open in your firewall")
        return True  # 不阻塞启动


def _open_port_windows(port: int) -> bool:
    """Windows: netsh 添加防火墙规则"""
    # 先检查规则是否已存在
    try:
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule",
             f"name={RULE_NAME}"],
            capture_output=True, text=True, timeout=5,
        )
        if RULE_NAME in result.stdout:
            logger.info(f"Firewall rule '{RULE_NAME}' already exists")
            return True
    except Exception:
        pass

    # 尝试添加规则
    cmd = [
        "netsh", "advfirewall", "firewall", "add", "rule",
        f"name={RULE_NAME}",
        f"dir=in",
        f"protocol=TCP",
        f"localport={port}",
        "action=allow",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            logger.info(f"Firewall rule '{RULE_NAME}' added for port {port}")
            return True
        else:
            # 可能是权限不足
            if "admin" in result.stderr.lower() or "0x5" in result.stderr:
                logger.warning(
                    f"Admin privileges required to open port {port}.\n"
                    f"  Run this in admin PowerShell:\n"
                    f"  New-NetFirewallRule -DisplayName '{RULE_NAME}' "
                    f"-Direction Inbound -LocalPort {port} -Protocol TCP -Action Allow"
                )
            else:
                logger.warning(f"Failed to add firewall rule: {result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        logger.warning("Firewall command timed out")
        return False
    except FileNotFoundError:
        # netsh 不可用（极老的系统）
        logger.warning("netsh not found, cannot configure firewall")
        return False


def close_port(port: int) -> bool:
    """删除防火墙规则，返回是否成功"""
    if platform.system() != "Windows":
        return True

    cmd = [
        "netsh", "advfirewall", "firewall", "delete", "rule",
        f"name={RULE_NAME}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            logger.info(f"Firewall rule '{RULE_NAME}' removed")
            return True
        else:
            logger.debug(f"Firewall cleanup: {result.stderr.strip()}")
            return False
    except Exception:
        return False
