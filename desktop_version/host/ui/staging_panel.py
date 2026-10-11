"""
暂存区面板 — 从从机打开的文件暂存、编辑、Ctrl+S 同步回从机
"""

import os
import tempfile
import base64
import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QTextEdit, QLabel,
    QPushButton, QMessageBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut

logger = logging.getLogger("host.ui.staging")

# 暂存文件本地目录
STAGING_DIR = os.path.join(tempfile.gettempdir(), "cloud_pc_staging")


class StagingPanel(QWidget):
    """暂存区：已打开文件列表 + 文本编辑器 + Ctrl+S 同步"""

    # 信号：请求下载文件 (remote_path → staging_panel 接收内容)
    file_opened = Signal(str)  # remote_path

    def __init__(self, connection):
        super().__init__()
        self._connection = connection
        self._files: dict[str, dict] = {}  # remote_path → {local_path, content, modified}
        self._current_file: str | None = None  # 当前编辑的文件 remote_path

        os.makedirs(STAGING_DIR, exist_ok=True)

        self._setup_ui()

        # Ctrl+S 快捷键
        self._save_shortcut = QShortcut(QKeySequence.Save, self)
        self._save_shortcut.activated.connect(self._sync_current)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 工具栏 ────────────────────────────────────
        toolbar = QHBoxLayout()

        self._btn_sync = QPushButton("Sync (Ctrl+S)")
        self._btn_sync.setToolTip("将当前文件同步回从机")
        self._btn_sync.clicked.connect(self._sync_current)
        toolbar.addWidget(self._btn_sync)

        self._btn_close = QPushButton("Close File")
        self._btn_close.clicked.connect(self._close_current)
        toolbar.addWidget(self._btn_close)

        toolbar.addStretch()
        self._sync_status = QLabel("")
        toolbar.addWidget(self._sync_status)

        layout.addLayout(toolbar)

        # ── 主区域：文件列表 + 编辑器 ─────────────────
        splitter = QSplitter(Qt.Horizontal)

        # 左侧文件列表
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Opened Files:"))
        self._file_list = QListWidget()
        self._file_list.currentRowChanged.connect(self._on_file_selected)
        left_layout.addWidget(self._file_list, 1)
        splitter.addWidget(left)

        # 右侧编辑器
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Editor:"))
        self._editor = QTextEdit()
        self._editor.setFont(QFont("Consolas, Courier New", 11))
        self._editor.textChanged.connect(self._on_text_changed)
        right_layout.addWidget(self._editor, 1)
        splitter.addWidget(right)

        splitter.setSizes([200, 600])
        layout.addWidget(splitter, 1)

    # ── 公开方法 ──────────────────────────────────────

    def open_file(self, remote_path: str):
        """打开一个远程文件到暂存区（文件浏览器双击时调用）"""
        # 检查是否已经打开
        if remote_path in self._files:
            # 已打开，切换到它
            self._select_file(remote_path)
            return

        # 添加到列表
        name = os.path.basename(remote_path)
        item = QListWidgetItem(name)
        item.setData(Qt.UserRole, remote_path)
        self._file_list.addItem(item)

        # 记录文件信息，先空内容
        local_path = os.path.join(STAGING_DIR, name)
        self._files[remote_path] = {
            "local_path": local_path,
            "content": "",
            "modified": False,
            "chunks": [],
            "total_chunks": 1,
        }

        # 选中
        self._file_list.setCurrentItem(item)

        # 触发下载
        self.file_opened.emit(remote_path)

    def on_download_chunk(self, payload: dict):
        """收到下载分块"""
        remote_path = payload.get("path", "")
        info = self._files.get(remote_path)
        if info is None:
            return

        chunk = payload.get("chunk", 0)
        total = payload.get("total", 1)
        data = base64.b64decode(payload.get("data", ""))

        info["total_chunks"] = total
        info["chunks"].append((chunk, data))

    def on_download_complete(self, payload: dict):
        """下载完成，组装文件并显示"""
        remote_path = payload.get("path", "")
        info = self._files.get(remote_path)
        if info is None:
            return

        # 按 chunk 序号排序组装
        info["chunks"].sort(key=lambda x: x[0])
        data = b"".join(c[1] for c in info["chunks"])
        info["chunks"] = []

        # 尝试文本解码
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = f"[Binary file, {len(data)} bytes, cannot display]"

        info["content"] = text
        info["modified"] = False

        # 写到本地暂存
        with open(info["local_path"], "wb") as f:
            f.write(data)

        # 如果当前正在编辑此文件，更新编辑器
        if self._current_file == remote_path:
            self._editor.setPlainText(text)
            self._update_title()

    # ── 事件处理 ──────────────────────────────────────

    def _on_file_selected(self, row: int):
        """切换选中的文件"""
        if row < 0:
            return
        item = self._file_list.item(row)
        remote_path = item.data(Qt.UserRole)
        self._select_file(remote_path)

    def _select_file(self, remote_path: str):
        """切换到指定文件"""
        # 保存当前文件内容
        if self._current_file and self._current_file in self._files:
            info = self._files[self._current_file]
            if info["modified"]:
                info["content"] = self._editor.toPlainText()

        self._current_file = remote_path
        info = self._files[remote_path]

        # 显示内容
        self._editor.blockSignals(True)
        self._editor.setPlainText(info["content"])
        self._editor.blockSignals(False)

        info["modified"] = False  # 刚加载，不算修改
        self._update_title()

    def _on_text_changed(self):
        """编辑器内容变化"""
        if self._current_file and self._current_file in self._files:
            info = self._files[self._current_file]
            if not info["modified"]:
                info["modified"] = True
                self._update_title()

    def _update_title(self):
        """更新标题：文件名 + 修改标记"""
        if self._current_file and self._current_file in self._files:
            info = self._files[self._current_file]
            name = os.path.basename(self._current_file)
            display = f"{name}{' *' if info['modified'] else ''}"
            self._sync_status.setText(display)

    def _sync_current(self):
        """Ctrl+S：将当前文件同步回从机"""
        if not self._current_file:
            return
        if not self._connection.is_connected:
            QMessageBox.warning(self, "Error", "Not connected to slave")
            return

        info = self._files.get(self._current_file)
        if info is None:
            return

        # 获取编辑器最新内容
        content = self._editor.toPlainText()
        data = content.encode("utf-8")

        # 分块上传
        remote_path = self._current_file
        chunk_size = 64 * 1024
        total_chunks = max(1, (len(data) + chunk_size - 1) // chunk_size)

        logger.info(f"Syncing {remote_path} ({len(data)} bytes, {total_chunks} chunks)")

        for i in range(total_chunks):
            chunk_data = data[i * chunk_size:(i + 1) * chunk_size]
            encoded = base64.b64encode(chunk_data).decode("ascii")
            self._connection.schedule_send(0x0103, {  # TYPE_UPLOAD
                "path": remote_path,
                "chunk": i,
                "total": total_chunks,
                "size": len(chunk_data),
                "data": encoded,
            })

        # 更新本地暂存文件
        with open(info["local_path"], "wb") as f:
            f.write(data)

        info["content"] = content
        info["modified"] = False
        self._update_title()

        logger.info(f"Sync complete: {remote_path}")

    def _close_current(self):
        """关闭当前文件"""
        if not self._current_file:
            return

        info = self._files.get(self._current_file)
        if info and info["modified"]:
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                f"Save changes to {os.path.basename(self._current_file)} before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            )
            if reply == QMessageBox.Cancel:
                return
            if reply == QMessageBox.Save:
                self._sync_current()

        # 从列表中移除
        for i in range(self._file_list.count()):
            item = self._file_list.item(i)
            if item.data(Qt.UserRole) == self._current_file:
                self._file_list.takeItem(i)
                break

        self._files.pop(self._current_file, None)
        self._current_file = None
        self._editor.clear()
        self._update_title()

        # 选中下一个
        if self._file_list.count() > 0:
            self._file_list.setCurrentRow(0)

    def cleanup(self):
        """清理暂存文件"""
        for info in self._files.values():
            try:
                os.remove(info["local_path"])
            except Exception:
                pass
        self._files.clear()
