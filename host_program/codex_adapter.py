"""Local-only Codex terminal adapter. No slave sockets or device networking."""
from __future__ import annotations

import ctypes
import re
import subprocess
import time
import pyatspi
from AR.alxr_integration.screenshot_relay import (
    current_codex_window_id, desktop_is_locked, paste_to_current_window,
    focus_codex_window,
)

class Transcript:
    """Keep up to 500 observed terminal lines (at most 64K characters)."""

    def __init__(self):
        self.previous: list[str] = []
        self.lines: list[str] = []

    def update(self, snapshot: str) -> str:
        incoming = snapshot.splitlines()
        if incoming != self.previous:
            overlap = 0
            for size in range(min(len(self.previous), len(incoming)), 0, -1):
                if self.previous[-size:] == incoming[:size]:
                    overlap = size
                    break
            if not self.lines:
                self.lines = incoming[:]
            elif overlap:
                self.lines.extend(incoming[overlap:])
            elif (len(incoming) == len(self.previous) and
                  sum(a == b for a, b in zip(incoming, self.previous)) >= len(incoming) // 2):
                # In-place redraw (spinner/progress): replace the visible tail.
                self.lines[-len(self.previous):] = incoming
            elif incoming != self.previous:
                # Terminal was cleared or scrolled by more than one screen.
                self.lines.extend(["", "… 终端画面跳转 …", *incoming])
            self.previous = incoming
        self.lines = self.lines[-500:]
        while len("\n".join(self.lines)) > 63_000 and self.lines:
            self.lines.pop(0)
        return "\n".join(self.lines)


class StopGuard:
    """At most one Escape per observed busy episode; idle never sends Escape."""

    def __init__(self):
        self.busy = False
        self.stopped = False
        self.idle_since: float | None = None

    def observe(self, busy: bool) -> None:
        if busy:
            if not self.busy:
                self.busy = True
                self.stopped = False
            self.idle_since = None
        elif self.busy:
            if self.idle_since is None:
                self.idle_since = time.monotonic()
            elif time.monotonic() - self.idle_since >= 2.0:
                self.busy = False
                self.stopped = False
                self.idle_since = None


def codex_is_busy() -> bool:
    target = current_codex_window_id()
    if target is None:
        return False
    title = subprocess.run(["xprop", "-id", hex(target), "WM_NAME"],
                           capture_output=True, text=True, check=True, timeout=3).stdout
    # Codex places a Braille spinner before this terminal's title while working.
    return bool(re.search(r'"[\u2800-\u28ff]\s+创建 AR 窗口程序', title))


def escape_current_window() -> None:
    """Send one Escape only after checking target identity and busy title again."""
    target = current_codex_window_id()
    if target is None or not codex_is_busy():
        raise RuntimeError("Codex 当前未运行；未发送 Esc")
    display = ctypes.CDLL("libX11.so.6")
    xtest = ctypes.CDLL("libXtst.so.6")
    display.XOpenDisplay.restype = ctypes.c_void_p
    display.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    display.XKeysymToKeycode.restype = ctypes.c_ubyte
    display.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    display.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    display.XGetInputFocus.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int)]
    display.XFlush.argtypes = [ctypes.c_void_p]
    display.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    display.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xtest.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    handle = display.XOpenDisplay(None)
    if not handle:
        raise RuntimeError("无法打开 X11 显示")
    try:
        key = display.XKeysymToKeycode(handle, 0xff1b)
        if not key:
            raise RuntimeError("无法解析 Esc 键")
        focus_codex_window(display, handle, target)
        if desktop_is_locked() or not codex_is_busy():
            raise RuntimeError("当前窗口不再运行；未发送 Esc")
        xtest.XTestFakeKeyEvent(handle, key, 1, 0)
        xtest.XTestFakeKeyEvent(handle, key, 0, 0)
        display.XFlush(handle)
        display.XSync(handle, 0)
    finally:
        display.XCloseDisplay(handle)


def current_terminal_text() -> str:
    """Read the exact visible terminal text via AT-SPI, never by OCR."""
    if desktop_is_locked() or current_codex_window_id() is None:
        raise RuntimeError("current Codex terminal unavailable or desktop locked")
    desktop = pyatspi.Registry.getDesktop(0)
    candidates = []
    for app in desktop:
        if (app.name or "") != "gnome-terminal-server":
            continue
        for window in app:
            title = window.name or ""
            if "创建 AR 窗口程序" in title and "old-linux" in title:
                candidates.append(window)
    if len(candidates) != 1:
        raise RuntimeError(f"expected one current Codex terminal, found {len(candidates)}")
    nodes = [candidates[0]]
    while nodes:
        node = nodes.pop(0)
        if node.getRoleName() == "terminal":
            text = node.queryText()
            value = text.getText(0, text.characterCount)
            if len(value) > 32_000:
                raise RuntimeError("terminal text exceeds 32K characters")
            return value
        nodes.extend(list(node))
    raise RuntimeError("terminal accessibility text is unavailable")
