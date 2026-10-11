"""
主机文件浏览器 — 左侧目录树 + 右侧文件列表 + 地址栏

用到的 PySide6 组件:
  QTreeView      — 目录树
  QTableView     — 文件列表（表格）
  QLineEdit      — 地址栏
  QSplitter      — 左右分栏
  QProgressBar   — 下载/上传进度
"""

import os
import base64
import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QPushButton,
    QLineEdit, QTreeView, QTableView, QHeaderView, QProgressBar,
    QLabel, QFileDialog, QMenu, QMessageBox, QAbstractItemView,
)
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QThread, Signal
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent

from common.protocol import (
    TYPE_LIST_DIR, TYPE_DOWNLOAD, TYPE_UPLOAD,
    TYPE_DELETE, TYPE_RENAME, TYPE_MKDIR,
)

logger = logging.getLogger("host.ui.file_browser")


# ── 文件表模型 ────────────────────────────────────────

class FileTableModel(QAbstractTableModel):
    """文件列表的 TableModel"""

    _headers = ["名称", "类型", "大小", "修改时间"]

    def __init__(self):
        super().__init__()
        self._entries: list[dict] = []

    def set_entries(self, entries: list[dict]):
        self.beginResetModel()
        self._entries = entries
        self.endResetModel()

    def get_entry(self, row: int) -> dict | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def rowCount(self, parent=QModelIndex()):
        return len(self._entries)

    def columnCount(self, parent=QModelIndex()):
        return 4

    def headerData(self, section, orientation, role):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self._headers[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        entry = self._entries[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                return entry.get("name", "")
            elif col == 1:
                return "📁" if entry.get("type") == "dir" else "📄"
            elif col == 2:
                size = entry.get("size", 0)
                if size == 0 and entry.get("type") == "dir":
                    return ""
                return _format_size(size)
            elif col == 3:
                import datetime
                mtime = entry.get("mtime", 0)
                return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        return None


def _format_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}" if size < 100 else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


# ── 文件浏览器主组件 ──────────────────────────────────

class FileBrowser(QWidget):
    """文件浏览器：目录树 + 文件列表"""

    # 双击文件 → 打开到暂存区
    file_open_requested = Signal(str)  # remote_path

    # 路径变化 → 同步任务面板工作目录
    path_changed = Signal(str)  # new_path

    def __init__(self, connection):
        super().__init__()
        self._connection = connection
        self._current_path = "/"
        self._active_downloads: dict[str, dict] = {}  # path → {f, total, got}
        self._download_dir: str | None = None

        self._setup_ui()
        self.setAcceptDrops(True)  # 允许拖拽上传

    def set_download_dir(self, path: str):
        self._download_dir = path

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ── 地址栏 ────────────────────────────────────
        addr_layout = QHBoxLayout()

        self._btn_up = QPushButton("⬆ 上一级")
        self._btn_up.clicked.connect(self._go_up)
        addr_layout.addWidget(self._btn_up)

        self._btn_refresh = QPushButton("🔄 刷新")
        self._btn_refresh.clicked.connect(self.refresh)
        addr_layout.addWidget(self._btn_refresh)

        self._addr_bar = QLineEdit()
        self._addr_bar.setText("/")
        self._addr_bar.returnPressed.connect(self._on_addr_enter)
        addr_layout.addWidget(self._addr_bar, 1)

        self._btn_mkdir = QPushButton("📁+")
        self._btn_mkdir.setToolTip("新建文件夹")
        self._btn_mkdir.clicked.connect(self._mkdir)
        addr_layout.addWidget(self._btn_mkdir)

        layout.addLayout(addr_layout)

        # ── 文件表格 ──────────────────────────────────
        self._table_model = FileTableModel()
        self._table = QTableView()
        self._table.setModel(self._table_model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setSortingEnabled(False)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.doubleClicked.connect(self._on_item_double_clicked)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

        # 表头
        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.resizeSection(1, 60)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.resizeSection(2, 100)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.resizeSection(3, 150)

        layout.addWidget(self._table, 1)

        # ── 进度条 ────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress_label = QLabel("")
        self._progress_label.setVisible(False)
        progress_layout = QHBoxLayout()
        progress_layout.addWidget(self._progress_label)
        progress_layout.addWidget(self._progress, 1)
        layout.addLayout(progress_layout)

    # ── 导航 ──────────────────────────────────────────

    def refresh(self):
        """刷新当前目录"""
        self._list_dir(self._current_path)

    def navigate_to(self, path: str):
        """跳转到指定路径"""
        self._list_dir(path)

    def _list_dir(self, path: str):
        """向从机请求列出目录"""
        if self._connection.is_connected:
            self._connection.schedule_send(TYPE_LIST_DIR, {"path": path})
            self._current_path = path
            self._addr_bar.setText(path)
            self.path_changed.emit(path)

    def _on_addr_enter(self):
        path = self._addr_bar.text().strip()
        if path:
            self.navigate_to(path)

    def _go_up(self):
        parent = os.path.dirname(self._current_path.rstrip("/"))
        if not parent:
            parent = "/"
        self.navigate_to(parent)

    def _on_item_double_clicked(self, index: QModelIndex):
        entry = self._table_model.get_entry(index.row())
        if entry is None:
            return
        if entry["type"] == "dir":
            # 进入目录
            new_path = self._current_path.rstrip("/") + "/" + entry["name"]
            self.navigate_to(new_path)
        else:
            # 打开文件到暂存区
            remote_path = self._current_path.rstrip("/") + "/" + entry["name"]
            self.file_open_requested.emit(remote_path)

    # ── 右键菜单 ──────────────────────────────────────

    def _show_context_menu(self, pos):
        index = self._table.currentIndex()
        entry = self._table_model.get_entry(index.row()) if index.isValid() else None

        menu = QMenu(self)

        if entry:
            if entry["type"] == "dir":
                open_action = menu.addAction("📂 打开")
                open_action.triggered.connect(
                    lambda: self.navigate_to(
                        self._current_path.rstrip("/") + "/" + entry["name"]
                    )
                )

            download_action = menu.addAction("⬇ 下载")
            download_action.triggered.connect(lambda: self._download_file(entry["name"]))

            menu.addSeparator()

            rename_action = menu.addAction("✏ 重命名")
            rename_action.triggered.connect(lambda: self._rename(entry["name"]))

            delete_action = menu.addAction("🗑 删除")
            delete_action.triggered.connect(lambda: self._delete(entry["name"]))

        menu.addSeparator()
        refresh_action = menu.addAction("🔄 刷新")
        refresh_action.triggered.connect(self.refresh)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    # ── 下载 ──────────────────────────────────────────

    def _download_file(self, name: str):
        """请求下载文件"""
        path = self._current_path.rstrip("/") + "/" + name
        local_dir = self._choose_download_dir()
        if not local_dir:
            return
        local_path = os.path.join(local_dir, name)

        # 打开本地文件准备写入
        f = open(local_path, "wb")
        self._active_downloads[path] = {"f": f, "total": 1, "got": 0, "local_path": local_path}

        if self._connection.is_connected:
            self._connection.schedule_send(TYPE_DOWNLOAD, {"path": path})

    def on_download_chunk(self, payload: dict):
        """收到下载分块"""
        path = payload.get("path", "")
        chunk = payload.get("chunk", 0)
        total = payload.get("total", 1)
        data_b64 = payload.get("data", "")

        state = self._active_downloads.get(path)
        if state is None:
            # 可能之前就开始了
            return

        state["total"] = total
        state["f"].write(base64.b64decode(data_b64))
        state["got"] = chunk + 1

        # 更新进度条
        self._progress.setVisible(True)
        self._progress.setMaximum(total)
        self._progress.setValue(state["got"])
        self._progress_label.setVisible(True)
        self._progress_label.setText(f"下载: {os.path.basename(path)}")

    def on_download_complete(self, payload: dict):
        """下载完成"""
        path = payload.get("path", "")
        state = self._active_downloads.pop(path, None)
        if state:
            state["f"].close()
            logger.info(f"Downloaded: {state['local_path']}")

        # 隐藏进度条
        if not self._active_downloads:
            self._progress.setVisible(False)
            self._progress_label.setVisible(False)

    # ── 上传（拖拽） ──────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            local_path = url.toLocalFile()
            if os.path.isfile(local_path):
                self._upload_file(local_path)

    def _upload_file(self, local_path: str):
        """上传本地文件到从机当前目录"""
        name = os.path.basename(local_path)
        remote_path = self._current_path.rstrip("/") + "/" + name
        total_size = os.path.getsize(local_path)
        chunk_size = 64 * 1024
        total_chunks = max(1, (total_size + chunk_size - 1) // chunk_size)

        logger.info(f"Upload: {local_path} → {remote_path} ({total_chunks} chunks)")

        def do_upload():
            with open(local_path, "rb") as f:
                for i in range(total_chunks):
                    data = f.read(chunk_size)
                    encoded = base64.b64encode(data).decode("ascii")
                    self._connection.schedule_send(TYPE_UPLOAD, {
                            "path": remote_path,
                            "chunk": i,
                            "total": total_chunks,
                            "size": len(data),
                            "data": encoded,
                        })
                    # 小延迟避免网络拥塞
                    import time
                    time.sleep(0.001)

        upload_thread = QThread()
        upload_thread.run = do_upload
        upload_thread.start()

    def on_upload_ack(self, payload: dict):
        """上传确认"""
        logger.info(f"Upload OK: {payload.get('path')}")
        self.refresh()

    # ── 删除 ──────────────────────────────────────────

    def _delete(self, name: str):
        path = self._current_path.rstrip("/") + "/" + name
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定删除 {name}？\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes and self._connection.is_connected:
            self._connection.schedule_send(TYPE_DELETE, {"path": path})

    def on_operation_ack(self, payload: dict):
        """操作确认（删除/重命名/mkdir）"""
        logger.info(f"Operation {payload.get('operation')} OK: {payload.get('path')}")
        self.refresh()

    # ── 重命名 ────────────────────────────────────────

    def _rename(self, old_name: str):
        from PySide6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(
            self, "重命名", "新名称:", text=old_name
        )
        if ok and new_name and new_name != old_name:
            path = self._current_path.rstrip("/") + "/" + old_name
            if self._connection.is_connected:
                self._connection.schedule_send(TYPE_RENAME, {
                        "path": path,
                        "new_name": new_name,
                    })

    # ── 新建文件夹 ────────────────────────────────────

    def _mkdir(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "新建文件夹", "文件夹名称:"
        )
        if ok and name:
            path = self._current_path.rstrip("/") + "/" + name
            if self._connection.is_connected:
                self._connection.schedule_send(TYPE_MKDIR, {"path": path})

    # ── 从机响应 ──────────────────────────────────────

    def on_list_dir_result(self, payload: dict):
        """收到目录列表"""
        self._current_path = payload.get("path", self._current_path)
        self._addr_bar.setText(self._current_path)
        self._table_model.set_entries(payload.get("entries", []))

    def on_file_stat_result(self, payload: dict):
        """收到文件信息"""
        pass  # 暂不处理

    # ── 辅助 ──────────────────────────────────────────

    def _choose_download_dir(self) -> str | None:
        """选择下载目录"""
        from host.config import DOWNLOAD_DIR
        path = self._download_dir or DOWNLOAD_DIR
        os.makedirs(path, exist_ok=True)
        return path

    def cleanup(self):
        """清理残留下载"""
        for state in self._active_downloads.values():
            try:
                state["f"].close()
            except Exception:
                pass
        self._active_downloads.clear()

