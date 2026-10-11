"""
Desktop host windows:
- Manager window for slave list and common actions
- One independent window per slave for files/staging/tasks
"""

import logging

from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QLabel, QMessageBox,
    QVBoxLayout, QWidget, QFrame,
    QListWidget, QListWidgetItem, QPushButton, QHBoxLayout,
    QInputDialog, QLineEdit, QFileDialog,
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSettings
from PySide6.QtGui import QFont

from host.ui.file_browser import FileBrowser
from host.ui.task_panel import TaskPanel
from host.ui.staging_panel import StagingPanel
from host.connection import SlaveClient
from host.config import DOWNLOAD_DIR, DEFAULT_SLAVE_PORT

logger = logging.getLogger("host.ui")

SETTINGS_ORG = "CloudPC"
SETTINGS_APP = "Host"
SETTINGS_DOWNLOAD_DIR = "download_dir"


class _UiBridge(QObject):
    """Thread-safe bridge from async callbacks to widgets."""

    list_dir_result = Signal(object)
    download_chunk = Signal(object)
    download_complete = Signal(object)
    staging_download_chunk = Signal(object)
    staging_download_complete = Signal(object)
    upload_ack = Signal(object)
    file_stat_result = Signal(object)
    operation_ack = Signal(object)
    task_status = Signal(object)
    task_output = Signal(object)
    error_occurred = Signal(str, str)


class SlaveWorkspace:
    """Shared widget bundle for one slave."""

    def __init__(self, client: SlaveClient, parent: QWidget):
        self.client = client
        self.bridge = _UiBridge(parent)

        self.file_browser = FileBrowser(client)
        self.staging_panel = StagingPanel(client)
        self.task_panel = TaskPanel(client)

        self._staging_downloads: set[str] = set()
        self._connect_signals()
        self._register_callbacks()

    def _connect_signals(self):
        b = self.bridge
        b.list_dir_result.connect(self.file_browser.on_list_dir_result)
        b.download_chunk.connect(self.file_browser.on_download_chunk)
        b.download_complete.connect(self.file_browser.on_download_complete)
        b.staging_download_chunk.connect(self.staging_panel.on_download_chunk)
        b.staging_download_complete.connect(self.staging_panel.on_download_complete)
        b.file_stat_result.connect(self.file_browser.on_file_stat_result)
        b.operation_ack.connect(self.file_browser.on_operation_ack)
        b.task_status.connect(self.task_panel.on_task_status)
        b.task_output.connect(self.task_panel.on_task_output)

    def _register_callbacks(self):
        conn = self.client
        bridge = self.bridge

        @conn.on_message(0x0181)
        async def _(payload):
            bridge.list_dir_result.emit(payload)

        @conn.on_message(0x0182)
        async def _(payload):
            path = payload.get("path", "")
            if path in self._staging_downloads:
                bridge.staging_download_chunk.emit(payload)
            else:
                bridge.download_chunk.emit(payload)

        @conn.on_message(0x0183)
        async def _(payload):
            self._staging_downloads.discard(payload.get("path", ""))
            bridge.staging_download_complete.emit(payload)
            bridge.download_complete.emit(payload)

        @conn.on_message(0x0184)
        async def _(payload):
            bridge.upload_ack.emit(payload)

        @conn.on_message(0x0185)
        async def _(payload):
            bridge.file_stat_result.emit(payload)

        @conn.on_message(0x0186)
        async def _(payload):
            bridge.operation_ack.emit(payload)

        @conn.on_message(0x0202)
        async def _(payload):
            bridge.task_status.emit(payload)

        @conn.on_message(0x0203)
        async def _(payload):
            bridge.task_output.emit(payload)

        @conn.on_message(0x0FFF)
        async def _(payload):
            bridge.error_occurred.emit(
                payload.get("code", "ERROR"),
                payload.get("message", "Unknown error"),
            )

    def open_file_in_staging(self, remote_path: str):
        self._staging_downloads.add(remote_path)
        self.staging_panel.open_file(remote_path)
        self.client.schedule_send(0x0102, {"path": remote_path})

    def cleanup(self):
        self.file_browser.cleanup()
        self.staging_panel.cleanup()


class SlaveWindow(QMainWindow):
    """Independent window for a single slave."""

    def __init__(self, client: SlaveClient, workspace: SlaveWorkspace, parent=None):
        super().__init__(parent)
        self.client = client
        self.workspace = workspace

        self._conn_banner = QFrame()
        self._conn_banner.setFrameStyle(QFrame.StyledPanel)
        self._conn_banner.setFixedHeight(32)
        banner_layout = QVBoxLayout(self._conn_banner)
        banner_layout.setContentsMargins(12, 4, 12, 4)
        self._conn_label = QLabel("")
        self._conn_label.setFont(QFont("Segoe UI", 10))
        banner_layout.addWidget(self._conn_label)

        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(False)
        self._tabs.addTab(workspace.file_browser, "Files")
        self._tabs.addTab(workspace.staging_panel, "Staging")
        self._tabs.addTab(workspace.task_panel, "Tasks")

        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._conn_banner)
        layout.addWidget(self._tabs, 1)
        self.setCentralWidget(wrapper)

        self._status_bar = QStatusBar()
        self._status_label = QLabel("Ready")
        self._status_bar.addWidget(self._status_label)
        self.setStatusBar(self._status_bar)

        self.resize(1100, 760)
        self.refresh_status()

    def refresh_status(self):
        connected = self.client.is_connected
        name = self.client.slave_name or self.client.address
        state = "ONLINE" if connected else self.client.state.upper()
        self.setWindowTitle(f"{name} - Cloud PC")
        self._conn_label.setText(f"{name} | {state} | {self.client.address}")
        self._status_label.setText(f"State: {state}")
        if connected:
            self._conn_banner.setStyleSheet(
                "QFrame { background-color: #2e7d32; color: white; }"
            )
        else:
            self._conn_banner.setStyleSheet(
                "QFrame { background-color: #616161; color: white; }"
            )

    def closeEvent(self, event):
        event.ignore()
        self.hide()


class MainWindow(QMainWindow):
    """Manager window. Each slave opens in its own independent window."""

    def __init__(self, clients: list[SlaveClient], on_add_slave=None, parent=None):
        super().__init__(parent)
        self._clients: dict[str, SlaveClient] = {}
        self._workspaces: dict[str, SlaveWorkspace] = {}
        self._slave_windows: dict[str, SlaveWindow] = {}
        self._on_add_slave = on_add_slave
        self._settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self._download_dir = self._load_download_dir()

        self._setup_ui()
        self.setWindowTitle("Cloud PC - Manager")
        self.resize(420, 720)

        for client in clients:
            self._add_slave_client(client)

        self._update_status()

    def _setup_ui(self):
        self._conn_banner = QFrame()
        self._conn_banner.setFrameStyle(QFrame.StyledPanel)
        self._conn_banner.setFixedHeight(32)
        banner_layout = QVBoxLayout(self._conn_banner)
        banner_layout.setContentsMargins(12, 4, 12, 4)
        self._conn_label = QLabel("No slaves connected")
        self._conn_label.setFont(QFont("Segoe UI", 10))
        banner_layout.addWidget(self._conn_label)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        header.addWidget(QLabel("Slaves"))

        add_btn = QPushButton("+")
        add_btn.setFixedWidth(30)
        add_btn.setToolTip("Add slave (ip:port)")
        add_btn.clicked.connect(self._add_slave_dialog)
        header.addWidget(add_btn)

        self._download_dir_btn = QPushButton("D")
        self._download_dir_btn.setFixedWidth(30)
        self._download_dir_btn.setToolTip("Set download folder")
        self._download_dir_btn.clicked.connect(self._choose_download_dir)
        header.addWidget(self._download_dir_btn)

        self._open_btn = QPushButton("O")
        self._open_btn.setFixedWidth(30)
        self._open_btn.setToolTip("Open selected slave window")
        self._open_btn.clicked.connect(self._open_selected_slave_window)
        header.addWidget(self._open_btn)

        self._disconnect_btn = QPushButton("X")
        self._disconnect_btn.setFixedWidth(30)
        self._disconnect_btn.setToolTip("Disconnect selected slave")
        self._disconnect_btn.clicked.connect(self._disconnect_slave)
        header.addWidget(self._disconnect_btn)

        content_layout.addLayout(header)

        self._slave_list = QListWidget()
        self._slave_list.itemDoubleClicked.connect(lambda _: self._open_selected_slave_window())
        content_layout.addWidget(self._slave_list, 1)

        self._hint_label = QLabel("Double-click a slave to open its own window.")
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet("color: #607080; padding: 8px;")
        content_layout.addWidget(self._hint_label)

        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(0)
        wrapper_layout.addWidget(self._conn_banner)
        wrapper_layout.addWidget(content, 1)
        self.setCentralWidget(wrapper)

        self._status_bar = QStatusBar()
        self._status_label = QLabel("Ready")
        self._status_bar.addWidget(self._status_label)
        self.setStatusBar(self._status_bar)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(2000)

    def _add_slave_dialog(self):
        text, ok = QInputDialog.getText(
            self,
            "Add Slave",
            "Slave address (ip:port, default port 9527):",
            QLineEdit.Normal,
            "",
        )
        if not ok or not text.strip():
            return

        text = text.strip()
        if ":" in text:
            ip, port_text = text.rsplit(":", 1)
            port = int(port_text)
        else:
            ip = text
            port = DEFAULT_SLAVE_PORT

        client = SlaveClient(ip, port)
        self._add_slave_client(client)

        if hasattr(self, "_loop_ref"):
            client.set_loop(self._loop_ref)
            import asyncio as aio
            aio.run_coroutine_threadsafe(client.run(), self._loop_ref)

    def _add_slave_client(self, client: SlaveClient):
        self._clients[client.slave_id] = client

        workspace = SlaveWorkspace(client, self)
        workspace.file_browser.set_download_dir(self._download_dir)
        workspace.file_browser.file_open_requested.connect(workspace.open_file_in_staging)
        workspace.file_browser.path_changed.connect(workspace.task_panel.set_cwd)
        self._workspaces[client.slave_id] = workspace

        slave_window = SlaveWindow(client, workspace, self)
        self._slave_windows[client.slave_id] = slave_window

        item = QListWidgetItem(client.address)
        item.setData(Qt.UserRole, client.slave_id)
        self._slave_list.addItem(item)
        self._slave_list.setCurrentRow(self._slave_list.count() - 1)

        self._show_slave_window(client.slave_id)

        if self._on_add_slave:
            self._on_add_slave(client)

    def _selected_slave_id(self) -> str | None:
        row = self._slave_list.currentRow()
        if row < 0:
            return None
        item = self._slave_list.item(row)
        return item.data(Qt.UserRole)

    def _show_slave_window(self, slave_id: str):
        window = self._slave_windows.get(slave_id)
        if not window:
            return
        window.refresh_status()
        window.show()
        window.raise_()
        window.activateWindow()

    def _open_selected_slave_window(self):
        slave_id = self._selected_slave_id()
        if slave_id:
            self._show_slave_window(slave_id)

    def _disconnect_slave(self):
        row = self._slave_list.currentRow()
        if row < 0:
            return

        item = self._slave_list.item(row)
        slave_id = item.data(Qt.UserRole)
        client = self._clients.get(slave_id)
        if not client or not client.is_connected:
            return

        reply = QMessageBox.question(
            self,
            "Disconnect",
            f"Disconnect and shutdown {client.slave_name or client.address}?\n"
            f"This will remove the firewall rule on the slave.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        client.schedule_send(0x00FF, {"reason": "host_disconnect"})
        self._slave_list.takeItem(row)

        window = self._slave_windows.pop(slave_id, None)
        if window:
            window.hide()
            window.deleteLater()

        workspace = self._workspaces.pop(slave_id, None)
        if workspace:
            workspace.cleanup()

        self._clients.pop(slave_id, None)

    def _load_download_dir(self) -> str:
        stored = self._settings.value(SETTINGS_DOWNLOAD_DIR, DOWNLOAD_DIR, type=str)
        return stored or DOWNLOAD_DIR

    def _save_download_dir(self, path: str):
        self._download_dir = path
        self._settings.setValue(SETTINGS_DOWNLOAD_DIR, path)
        for workspace in self._workspaces.values():
            workspace.file_browser.set_download_dir(path)
        self._status_label.setText(f"Download folder: {path}")

    def _choose_download_dir(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Download Folder",
            self._download_dir,
        )
        if path:
            self._save_download_dir(path)

    def _update_status(self):
        connected = sum(1 for client in self._clients.values() if client.is_connected)
        total = len(self._clients)

        if connected > 0:
            names = [client.slave_name or client.address for client in self._clients.values() if client.is_connected]
            self._conn_label.setText(f"Connected: {', '.join(names)} ({connected}/{total})")
            self._conn_banner.setStyleSheet(
                "QFrame { background-color: #2e7d32; color: white; }"
            )
        else:
            self._conn_label.setText(f"No slaves connected ({connected}/{total}). Click + to add.")
            self._conn_banner.setStyleSheet(
                "QFrame { background-color: #616161; color: white; }"
            )

        for i in range(self._slave_list.count()):
            item = self._slave_list.item(i)
            slave_id = item.data(Qt.UserRole)
            client = self._clients.get(slave_id)
            if not client:
                continue
            status = "G" if client.is_connected else " "
            item.setText(f"[{status}] {client.slave_name or client.address}")
            window = self._slave_windows.get(slave_id)
            if window:
                window.refresh_status()

    def closeEvent(self, event):
        for window in self._slave_windows.values():
            window.hide()
        for workspace in self._workspaces.values():
            workspace.cleanup()
        super().closeEvent(event)
