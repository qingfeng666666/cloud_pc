"""
从机任务服务 — 命令执行、进程管理、输出转发

支持 shell 模式：命令通过 cmd.exe（Windows）/ /bin/sh（Linux）执行，
可以使用管道、重定向、dir/cd/set 等 shell 内置命令。
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
import sys
import re
from typing import Dict, List, Optional

logger = logging.getLogger("slave.task")


class CommandPolicyError(PermissionError):
    """Raised when a raw PowerShell request exceeds read-only capability."""


_POWERSHELL_BLOCKED = re.compile(
    r"(?:^|[^a-z0-9_-])(?:"
    r"set-content|add-content|out-file|new-item|remove-item|rename-item|"
    r"move-item|copy-item|clear-content|set-item|set-acl|"
    r"start-process|invoke-expression|invoke-command|invoke-webrequest|"
    r"reg(?:\.exe)?|sc(?:\.exe)?|schtasks(?:\.exe)?|"
    r"cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh(?:\.exe)?|"
    r"mshta(?:\.exe)?|rundll32(?:\.exe)?|wscript(?:\.exe)?|cscript(?:\.exe)?|"
    r"python(?:\.exe)?|py(?:\.exe)?"
    r")(?:$|[^a-z0-9_-])",
    re.IGNORECASE,
)
_POWERSHELL_FILE = re.compile(r"(?:^|[\s'\"])(?:\.?\\|[a-z]:\\|[^\s]+\.(?:ps1|psm1|bat|cmd|exe|com|dll|vbs|js))(?:$|[\s'\"])", re.IGNORECASE)


def _validate_powershell(cmd: str, args: List[str]) -> None:
    """Permit only simple read-only PowerShell queries on the runtime path.

    Text filters cannot safely turn arbitrary PowerShell into a sandbox.  This
    small allow-list therefore rejects chaining, redirection, encoded input,
    script files, child-process launch and known mutation cmdlets.  File
    changes must use TYPE_FILE_MUTATE; application launch belongs to a future
    typed capability rather than raw shell.
    """
    executable = os.path.basename(cmd).lower()
    if executable not in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        return
    text = " ".join(str(arg) for arg in args)
    lowered = text.lower()
    if any(flag in lowered for flag in ("-file", "-encodedcommand", "-enc", "-executionpolicy", "-command")):
        # ``-Command`` is allowed only below after extracting a query; encoded
        # commands and policy override are never accepted.
        if "-command" not in lowered or any(flag in lowered for flag in ("-file", "-encodedcommand", "-enc", "-executionpolicy")):
            raise CommandPolicyError("PowerShell script/encoded execution is disabled")
    if any(token in text for token in (";", "&", "|", ">", "<", "`", "$()")):
        raise CommandPolicyError("PowerShell chaining, pipelines and redirection are disabled")
    if _POWERSHELL_BLOCKED.search(text) or _POWERSHELL_FILE.search(text):
        raise CommandPolicyError("PowerShell mutation or direct file execution is disabled")
    query = re.sub(r"(?i)^.*?-command\s+", "", text).strip().lstrip("'\"")
    if not re.match(r"(?i)^(get-|test-path|select-|where-|format-|measure-|resolve-path|write-output)\b", query):
        raise CommandPolicyError("Only read-only PowerShell query cmdlets are allowed")


class TaskService:
    """从机任务执行服务"""

    def __init__(self):
        # PyInstaller on the slave currently uses Python 3.8.
        self._running: Dict[str, subprocess.Popen] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def exec(self, task_id: str, cmd: str, args: List[str],
                   cwd: str, env: Optional[dict], send):
        """执行命令并实时回传输出（shell 模式）"""
        self._loop = asyncio.get_event_loop()

        try:
            _validate_powershell(cmd, args)
        except CommandPolicyError as exc:
            logger.warning("Blocked PowerShell task [%s]: %s", task_id, exc)
            await send(0x0FFF, {"code": "COMMAND_POLICY_DENIED", "message": str(exc), "task_id": task_id})
            return

        # 工作目录
        full_cwd = cwd if cwd else os.getcwd()
        if not os.path.isdir(full_cwd):
            await send(0x0FFF, {
                "code": "INVALID_CWD",
                "message": f"Working directory not found: {cwd}",
                "task_id": task_id,
            })
            return

        # 拼接完整命令行
        arg_str = subprocess.list2cmdline(args) if args else ""
        command_line = f"{cmd} {arg_str}".strip()

        # 合并环境变量
        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)

        logger.info(f"Exec [{task_id}]: {command_line} (cwd={full_cwd})")

        try:
            proc = subprocess.Popen(
                command_line,
                cwd=full_cwd,
                env=proc_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                shell=True,   # 走系统 shell，支持管道/重定向/内置命令
            )
        except Exception as e:
            await send(0x0FFF, {
                "code": "EXEC_ERROR",
                "message": str(e),
                "task_id": task_id,
            })
            return

        self._running[task_id] = proc

        await send(0x0202, {
            "task_id": task_id,
            "status": "running",
        })

        # 两个线程分别读 stdout/stderr
        stdout_done = threading.Event()
        stderr_done = threading.Event()

        def read_stream(stream_name: str, pipe, done_event: threading.Event):
            try:
                for line in pipe:
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            send(0x0203, {
                                "task_id": task_id,
                                "stream": stream_name,
                                "text": line,
                            }),
                            self._loop,
                        )
            except ValueError:
                pass
            finally:
                done_event.set()

        threading.Thread(target=read_stream, args=("stdout", proc.stdout, stdout_done),
                         daemon=True).start()
        threading.Thread(target=read_stream, args=("stderr", proc.stderr, stderr_done),
                         daemon=True).start()

        # 等进程结束
        loop = self._loop

        def wait_process():
            exit_code = proc.wait()
            stdout_done.wait()
            stderr_done.wait()
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    send(0x0202, {
                        "task_id": task_id,
                        "status": "completed" if exit_code == 0 else "failed",
                        "exit_code": exit_code,
                    }),
                    loop,
                )
            self._running.pop(task_id, None)
            logger.info(f"Task [{task_id}] finished, exit_code={exit_code}")

        threading.Thread(target=wait_process, daemon=True).start()

    def kill(self, task_id: str) -> bool:
        """终止指定任务"""
        proc = self._running.get(task_id)
        if proc is None:
            return False

        logger.info(f"Killing task [{task_id}]")
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        except Exception as e:
            logger.warning(f"Error killing task [{task_id}]: {e}")

        self._running.pop(task_id, None)
        return True
