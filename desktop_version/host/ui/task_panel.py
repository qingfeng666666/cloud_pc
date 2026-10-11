"""
主机任务面板 — 命令输入 + 实时终端输出 + 任务历史

用到的 PySide6 组件:
  QLineEdit      — 命令输入框
  QPushButton    — 执行/终止按钮
  QTextEdit      — 终端输出区域（只读）
  QListWidget    — 任务历史列表
"""

import logging
import uuid
import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLineEdit, QPushButton, QTextEdit, QListWidget, QListWidgetItem,
    QLabel,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor, QTextCharFormat, QBrush

from common.protocol import TYPE_EXEC, TYPE_TASK_KILL

logger = logging.getLogger("host.ui.task_panel")


class TaskPanel(QWidget):
    """任务面板"""

    def __init__(self, connection):
        super().__init__()
        self._connection = connection
        self._active_task_id: str | None = None
        self._task_history: dict[str, dict] = {}  # task_id → {cmd, status, exit_code}

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ── 命令输入行 ────────────────────────────────
        cmd_layout = QHBoxLayout()

        cmd_layout.addWidget(QLabel("命令:"))

        self._cmd_input = QLineEdit()
        self._cmd_input.setPlaceholderText("输入命令，如: python render.py --scene=final")
        self._cmd_input.returnPressed.connect(self._exec_command)
        cmd_layout.addWidget(self._cmd_input, 1)

        self._btn_exec = QPushButton("▶ 执行")
        self._btn_exec.clicked.connect(self._exec_command)
        cmd_layout.addWidget(self._btn_exec)

        self._btn_kill = QPushButton("⏹ 终止")
        self._btn_kill.setEnabled(False)
        self._btn_kill.clicked.connect(self._kill_task)
        cmd_layout.addWidget(self._btn_kill)

        layout.addLayout(cmd_layout)

        # ── 工作目录（跟随文件浏览器自动同步）──
        cwd_layout = QHBoxLayout()
        cwd_layout.addWidget(QLabel("Work dir:"))
        self._cwd_label = QLabel("/")
        cwd_layout.addWidget(self._cwd_label, 1)
        self._cwd_input_val = ""  # 内部保存当前 cwd
        layout.addLayout(cwd_layout)

        layout.addLayout(cwd_layout)

        # ── 主区域：输出 + 历史 ────────────────────────
        splitter = QSplitter(Qt.Horizontal)

        # 终端输出
        output_widget = QWidget()
        output_layout = QVBoxLayout(output_widget)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(QLabel("输出:"))

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFont(QFont("Consolas, Courier New", 10))
        # 暗色终端风格
        self._output.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #333;
            }
        """)
        output_layout.addWidget(self._output, 1)
        splitter.addWidget(output_widget)

        # 任务历史
        history_widget = QWidget()
        history_layout = QVBoxLayout(history_widget)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.addWidget(QLabel("历史:"))

        self._history_list = QListWidget()
        history_layout.addWidget(self._history_list, 1)
        splitter.addWidget(history_widget)

        splitter.setSizes([600, 250])
        layout.addWidget(splitter, 1)

    # ── 执行 ──────────────────────────────────────────

    def set_cwd(self, path: str):
        """文件浏览器切路径时同步工作目录"""
        self._cwd_input_val = path
        self._cwd_label.setText(path)

    def _exec_command(self):
        """执行命令"""
        cmd_text = self._cmd_input.text().strip()
        if not cmd_text:
            return
        if not self._connection.is_connected:
            self._append_output("❌ 未连接到从机\n", "red")
            return

        # 解析命令（简单按空格分割）
        parts = cmd_text.split()
        cmd = parts[0]
        args = parts[1:] if len(parts) > 1 else []

        task_id = str(uuid.uuid4())[:8]
        cwd = self._cwd_input_val

        # 清空输出区域
        self._output.clear()
        self._output.append(f"$ {cmd_text}\n")

        # 发送到从机
        self._connection.schedule_send(TYPE_EXEC, {
            "task_id": task_id,
            "cmd": cmd,
            "args": args,
            "cwd": cwd,
        })

        self._active_task_id = task_id
        self._btn_kill.setEnabled(True)
        self._btn_exec.setEnabled(False)

        # 加入历史
        self._task_history[task_id] = {
            "cmd": cmd_text,
            "status": "running",
            "time": datetime.datetime.now(),
        }
        self._refresh_history()

    def _kill_task(self):
        """终止当前任务"""
        if not self._active_task_id or not self._connection.is_connected:
            return

        self._connection.schedule_send(TYPE_TASK_KILL, {
            "task_id": self._active_task_id,
        })
        self._append_output("\n⚠ 已发送终止信号\n", "orange")

    # ── 从机响应 ──────────────────────────────────────

    def on_task_output(self, payload: dict):
        """收到任务输出"""
        stream = payload.get("stream", "stdout")
        text = payload.get("text", "")

        if stream == "stderr":
            self._append_output(text, "#ff8888")  # 浅红
        else:
            self._append_output(text, "#d4d4d4")  # 浅灰

    def on_task_status(self, payload: dict):
        """收到任务状态变更"""
        task_id = payload.get("task_id", "")
        status = payload.get("status", "")
        exit_code = payload.get("exit_code", None)

        if status == "running":
            self._append_output("── 任务已启动 ──\n", "#888")

        elif status == "completed":
            self._append_output(f"\n── 任务完成 (exit: {exit_code}) ──\n", "lightgreen")
            self._task_done(task_id, "completed", exit_code)

        elif status == "failed":
            self._append_output(f"\n── 任务失败 (exit: {exit_code}) ──\n", "red")
            self._task_done(task_id, "failed", exit_code)

        elif status == "killed":
            self._append_output(f"\n── 任务已被终止 ──\n", "orange")
            self._task_done(task_id, "killed", None)

    def _task_done(self, task_id: str, status: str, exit_code):
        """任务结束后的 UI 恢复"""
        if task_id == self._active_task_id:
            self._active_task_id = None
            self._btn_kill.setEnabled(False)
            self._btn_exec.setEnabled(True)

        history = self._task_history.get(task_id, {})
        history["status"] = status
        history["exit_code"] = exit_code
        self._refresh_history()

    # ── 输出 ──────────────────────────────────────────

    def _append_output(self, text: str, color: str = "#d4d4d4"):
        """追加输出文本"""
        self._output.moveCursor(self._output.textCursor().End)
        fmt = self._output.currentCharFormat()
        # 只对本次追加的文本设置颜色
        self._output.setTextColor(QColor(color))
        self._output.insertPlainText(text)
        # 滚动到底部
        self._output.moveCursor(self._output.textCursor().End)
        self._output.ensureCursorVisible()

    # ── 历史 ──────────────────────────────────────────

    def _refresh_history(self):
        """刷新历史列表"""
        self._history_list.clear()
        for task_id, info in reversed(list(self._task_history.items())):
            status = info.get("status", "unknown")
            icon = {"running": "⏳", "completed": "✅", "failed": "❌", "killed": "⛔"}.get(status, "❓")
            time_str = info.get("time", datetime.datetime.now()).strftime("%H:%M:%S")
            text = f"{icon} [{time_str}] {info['cmd']}"
            self._history_list.addItem(QListWidgetItem(text))
