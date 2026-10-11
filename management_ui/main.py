#!/usr/bin/env python3
"""Three-entry GTK management client; no direct slave connections or input injection."""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cloud_pc.management_ui.client import HostClient
from cloud_pc.management_ui.recent_dialogue import RecentDialogue
from cloud_pc.management_ui.conversation_preview import compact_conversation
from cloud_pc.shared_components.common.portable_config import ConfigStore
from cloud_pc.shared_components.common.release import __version__


def main():
    parser = argparse.ArgumentParser(description="云电脑管理界面")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--host-api", help="本机程序地址；写入配置的 role")
    parser.add_argument("--config", type=Path, help="management.json 路径；默认程序同目录")
    parser.add_argument("--smoke-test", action="store_true", help="Only construct the GUI, then close; no API requests")
    args = parser.parse_args()
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib, Gdk, GdkPixbuf

    try:
        store = ConfigStore('management', args.config)
        config = store.load()
    except (ValueError, OSError) as error: parser.error(str(error))
    if args.host_api:
        roles = {'http://127.0.0.1:19527': 'host', 'http://127.0.0.1:19528': 'slave'}
        if args.host_api not in roles: parser.error('管理界面只能访问本机19527/19528')
        config = store.update({'role': roles[args.host_api]})
    args.host_api = 'http://127.0.0.1:' + ('19527' if config['role'] == 'host' else '19528')

    class ManagementWindow(Gtk.Window):
        def __init__(self):
            super().__init__(title="云电脑 · 管理界面 " + __version__)
            self.set_default_size(900, 680)
            self.client = HostClient(args.host_api, config['host_ip'])
            self.executor = ThreadPoolExecutor(max_workers=3)
            self.page = "home"
            self.generation = 0
            self.polling = False
            self.action_pending = False
            self.closed = False
            self.revision = None
            self.host_session = None
            self.first_text = True
            self.provider = ""
            self.llm_session = ""
            self.selected_slave = None
            self.recent = RecentDialogue(store.path)
            self.connect("destroy", self.on_close)
            css = Gtk.CssProvider()
            css.load_from_data(b"window { background: #101820; color: #f1f4f6; } button { padding: 12px; } entry { min-height: 40px; } textview text { background: #202a32; color: #f1f4f6; } label { color: #f1f4f6; }")
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            self.root.set_border_width(18)
            self.add(self.root)
            self.show_home()
            if not args.smoke_test:
                self.executor.submit(self.client.activate)
            self.timer = GLib.timeout_add(1000, self.tick)

        def on_close(self, *_):
            self.closed = True
            if hasattr(self, "timer"):
                GLib.source_remove(self.timer)
            if not args.smoke_test:
                self.executor.submit(self.client.release)
            self.executor.shutdown(wait=False)
            Gtk.main_quit()

        def reset(self, page):
            self.page = page
            self.generation += 1
            self.polling = False
            for child in self.root.get_children():
                self.root.remove(child)

        def header(self, title):
            row = Gtk.Box(spacing=12)
            back = Gtk.Button(label="返回首页")
            back.connect("clicked", lambda *_: self.show_home())
            row.pack_start(back, False, False, 0)
            row.pack_start(Gtk.Label(label=title), False, False, 0)
            self.root.pack_start(row, False, False, 0)

        def blank_footer(self):
            blank = Gtk.Box()
            blank.set_size_request(-1, 64)
            self.root.pack_end(blank, False, False, 0)

        def show_home(self):
            self.reset("home")
            self.root.pack_start(Gtk.Label(label="云电脑控制界面"), False, False, 10)
            host_ip = Gtk.Entry()
            host_ip.set_placeholder_text("主机 IP")
            host_ip.set_text(config['host_ip'])
            self.root.pack_start(host_ip, False, False, 0)
            activate = Gtk.Button(label="保存并连接主机")
            def connect_host(*_):
                try:
                    value = store.update({'host_ip': host_ip.get_text().strip()})
                    config.update(value)
                    self.client.host_ip = value['host_ip']
                    self.work(lambda: self.client.activate(True), lambda result: activate.set_label(
                        "已连接主机" if result.get('ok') else result.get('error', '连接失败')))
                except Exception as exc:
                    activate.set_label(str(exc))
            activate.connect("clicked", connect_host)
            self.root.pack_start(activate, False, False, 0)
            add = Gtk.Button(label="添加从机")
            add.connect("clicked", lambda *_: self.add_slave())
            self.root.pack_start(add, False, False, 0)
            refresh = Gtk.Button(label="刷新从机列表")
            refresh.connect("clicked", lambda *_: self.load_slaves())
            self.root.pack_start(refresh, False, False, 0)
            self.slave_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.root.pack_start(self.slave_rows, True, True, 0)
            self.status = Gtk.Label(label="从主机读取从机列表…")
            self.root.pack_start(self.status, False, False, 0)
            for index, title in [(2, "LLM 对话界面"), (3, "主机运行状态")]:
                button = Gtk.Button(label=title)
                button.set_size_request(-1, 64)
                button.connect("clicked", lambda _, i=index: self.open_page(i))
                self.root.pack_start(button, False, False, 0)
            self.blank_footer()
            self.show_all()
            if not args.smoke_test: self.load_slaves()

        def load_slaves(self):
            self.work(self.client.slaves, self.apply_slaves)

        def apply_slaves(self, generation, result, failure):
            if generation != self.generation or self.page != 'home': return
            if failure or not result.get('ok'):
                self.status.set_text(failure or result.get('error', '读取失败')); return
            for child in self.slave_rows.get_children(): self.slave_rows.remove(child)
            for item in result.get('slaves', []):
                row = Gtk.Box(spacing=8)
                title = item.get('name', '') + ' · ' + item['address'] + (' · 在线' if item.get('connected') else ' · 离线')
                show = Gtk.Button(label=title)
                show.connect('clicked', lambda _, s=item: self.open_slave(s))
                row.pack_start(show, True, True, 0)
                remove = Gtk.Button(label='移除')
                remove.connect('clicked', lambda _, s=item: self.remove_slave(s))
                row.pack_start(remove, False, False, 0)
                self.slave_rows.pack_start(row, False, False, 0)
            self.status.set_text('列表来自主机 · 添加／移除保存到主机 host.json')
            self.show_all()

        def open_slave(self, item):
            self.selected_slave = item
            self.open_page(0)

        def add_slave(self):
            dialog = Gtk.Dialog(title='添加从机（保存到主机）', transient_for=self, modal=True)
            dialog.add_buttons('取消', Gtk.ResponseType.CANCEL, '添加', Gtk.ResponseType.OK)
            host = Gtk.Entry(); host.set_placeholder_text('从机 IPv4 地址')
            port = Gtk.Entry(); port.set_text('9527')
            dialog.get_content_area().pack_start(host, False, False, 6)
            dialog.get_content_area().pack_start(port, False, False, 6)
            dialog.show_all()
            if dialog.run() == Gtk.ResponseType.OK:
                try:
                    ip, value = host.get_text().strip(), int(port.get_text())
                    self.work(lambda: self.client.slaves('add', host=ip, port=value), self.apply_slaves)
                except ValueError: self.status.set_text('端口格式不正确')
            dialog.destroy()

        def remove_slave(self, item):
            dialog = Gtk.MessageDialog(transient_for=self, modal=True, buttons=Gtk.ButtonsType.OK_CANCEL,
                text='从主机配置移除 ' + item['address'] + '？不会删除从机文件。')
            if dialog.run() == Gtk.ResponseType.OK:
                self.work(lambda: self.client.slaves('remove', host=item['host'], port=item['port']), self.apply_slaves)
            dialog.destroy()

        def open_page(self, index):
            if index == 3:
                self.reset("activity")
                self.header("主机运行状态")
                self.scroll = Gtk.ScrolledWindow()
                self.activity_text = Gtk.TextView()
                self.activity_text.set_editable(False)
                self.activity_text.set_monospace(True)
                self.scroll.add(self.activity_text)
                self.root.pack_start(self.scroll, True, True, 0)
                self.status = Gtk.Label(label="每秒更新 · 最新记录在上")
                self.root.pack_start(self.status, False, False, 0)
                self.show_all()
            elif index == 2:
                self.providers()
            else:
                self.reset(f"slave{index}")
                self.header(f"从机界面{index + 1}")
                self.screenshot_rotation = 0
                self.screenshot_source = None
                rotate = Gtk.Button(label="旋转截图 ↻ 90°")
                rotate.connect('clicked', lambda *_: self.rotate_screenshot())
                self.root.pack_start(rotate, False, False, 0)
                self.image = Gtk.Image()
                self.root.pack_start(self.image, True, True, 0)
                host = (self.selected_slave or {}).get("host", "")
                self.status = Gtk.Label(label=f"等待截图 · {host}" if host else "请返回首页选择从机")
                self.status.set_line_wrap(True)
                self.root.pack_start(self.status, False, False, 0)
                self.blank_footer()
                self.show_all()
            if not args.smoke_test:
                self.tick()

        def providers(self):
            self.reset("providers")
            self.header("LLM 对话界面 · 用户在主机预先配置模型与认证")
            previous = self.recent.load()
            if previous:
                resume = Gtk.Button(label="返回上次对话 · " + previous['provider'])
                def restore(*_):
                    self.provider = previous['provider']
                    self.select_session(previous['session_id'])
                resume.connect("clicked", restore)
                self.root.pack_start(resume, False, False, 0)
            self.status = Gtk.Label(label="读取主机配置…")
            self.root.pack_end(self.status, False, False, 0)
            def loaded(generation, result, failure):
                if generation != self.generation: return
                if failure or not result.get("ok"):
                    self.status.set_text(failure or result.get("error", "API 失败")); return
                for item in result.get("providers", []):
                    p = item["id"]
                    button = Gtk.Button(label=p + ("" if item.get("enabled") else "（主机未启用）"))
                    button.set_sensitive(bool(item.get("enabled")))
                    button.connect("clicked", lambda _, name=p: self.sessions(name))
                    self.root.pack_start(button, False, False, 0)
                self.status.set_text("请选择 LLM"); self.show_all()
            if args.smoke_test:
                loaded(self.generation, {"ok": True, "providers": [
                    {"id": p, "enabled": True} for p in ["codex", "dsh", "opencode"]]}, None)
            else:
                self.work(lambda: self.client.llm("providers"), loaded)
            self.show_all()

        def sessions(self, provider):
            self.provider, self.llm_session = provider, ""
            self.reset("sessions")
            self.header(provider + " · 选择要继续的会话")
            back = Gtk.Button(label="返回 LLM 列表")
            back.connect("clicked", lambda *_: self.providers())
            self.root.pack_start(back, False, False, 0)
            new = Gtk.Button(label="新建会话")
            new.connect("clicked", lambda *_: self.create_session())
            self.root.pack_start(new, False, False, 0)
            refresh = Gtk.Button(label="刷新会话列表")
            refresh.connect("clicked", lambda *_: self.sessions(provider))
            self.root.pack_start(refresh, False, False, 0)
            scroller = Gtk.ScrolledWindow()
            self.session_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            scroller.add(self.session_box); self.root.pack_start(scroller, True, True, 0)
            self.status = Gtk.Label(label="读取会话…")
            self.root.pack_start(self.status, False, False, 0)
            self.load_sessions()
            self.show_all()

        def load_sessions(self, cursor=None):
            provider = self.provider
            def loaded(generation, result, failure):
                if generation != self.generation: return
                if failure or not result.get("ok"):
                    self.status.set_text(failure or result.get("error", "API 失败")); return
                for item in result.get("sessions", []):
                    sid = item["id"]
                    title = ' '.join(item.get("title", sid).split())
                    button = Gtk.Button(label=title[:160] + ('…' if len(title) > 160 else '') + "\n" + sid)
                    button.connect("clicked", lambda _, name=sid: self.select_session(name))
                    self.session_box.pack_start(button, False, False, 0)
                if result.get("next_cursor"):
                    following = result["next_cursor"]
                    more = Gtk.Button(label="更多会话")
                    more.connect("clicked", lambda *_: (more.destroy(), self.load_sessions(following)))
                    self.session_box.pack_start(more, False, False, 0)
                self.status.set_text("选择会话或新建"); self.show_all()
            self.work(lambda: self.client.llm("sessions", provider, cursor=cursor), loaded)

        def create_session(self):
            if self.action_pending: return
            self.action_pending = True
            provider = self.provider
            rid = "gtk-new-" + uuid.uuid4().hex
            def created(generation, result, failure):
                self.action_pending = False
                if generation != self.generation: return
                if failure or not result.get("ok"):
                    self.status.set_text(failure or result.get("error", "结果未确认，未重发")); return
                self.select_session(result["session_id"])
            self.work(lambda: self.client.llm("create", provider, request_id=rid), created)

        def select_session(self, sid):
            provider = self.provider
            def opened(generation, result, failure):
                if generation != self.generation: return
                if failure or not result.get("ok"):
                    self.status.set_text(failure or result.get("error", "API 失败")); return
                self.provider, self.llm_session = provider, sid
                try: self.recent.save(provider, sid)
                except OSError: pass
                self.show_codex()
            self.work(lambda: self.client.llm("open", provider, sid), opened)

        def show_codex(self):
            self.reset("codex")
            self.conversation_text = ""
            self.fold_expanded = False
            self.header(self.provider + " · 每秒更新文字")
            switch = Gtk.Button(label="切换会话")
            switch.connect("clicked", lambda *_: self.sessions(self.provider))
            self.root.pack_start(switch, False, False, 0)
            self.input = Gtk.Entry()
            self.input.set_placeholder_text("输入到选中会话（最多16KB）")
            self.root.pack_start(self.input, False, False, 0)
            row = Gtk.Box(spacing=12)
            self.send_button = Gtk.Button(label="SEND")
            self.stop_button = Gtk.Button(label="STOP")
            self.stop_button.set_sensitive(False)
            self.send_button.set_sensitive(not self.action_pending)
            self.send_button.connect("clicked", lambda *_: self.action("send"))
            self.stop_button.connect("clicked", lambda *_: self.action("stop"))
            row.pack_start(self.send_button, True, True, 0)
            row.pack_start(self.stop_button, True, True, 0)
            self.fold_button = Gtk.Button(label='展开全部')
            self.fold_button.connect('clicked', lambda *_: self.toggle_conversation())
            row.pack_start(self.fold_button, False, False, 0)
            self.root.pack_start(row, False, False, 0)
            self.scroll = Gtk.ScrolledWindow()
            self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
            self.textview = Gtk.TextView()
            self.textview.set_editable(False)
            self.textview.set_cursor_visible(False)
            self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            self.textview.set_monospace(True)
            self.textview.set_left_margin(12)
            self.textview.set_right_margin(12)
            self.scroll.add(self.textview)
            self.root.pack_start(self.scroll, True, True, 0)
            self.status = Gtk.Label(label="等待主机文字")
            self.status.set_line_wrap(True)
            self.root.pack_start(self.status, False, False, 0)
            self.action_status = Gtk.Label(label="")
            self.action_status.set_line_wrap(True)
            self.root.pack_start(self.action_status, False, False, 0)
            self.first_text = True
            self.revision = None
            self.show_all()

        def toggle_conversation(self):
            self.fold_expanded = not self.fold_expanded
            self.fold_button.set_label('收起长内容' if self.fold_expanded else '展开全部')
            adjustment = self.scroll.get_vadjustment()
            old = adjustment.get_value()
            self.textview.get_buffer().set_text(compact_conversation(self.conversation_text, self.fold_expanded))
            GLib.idle_add(lambda: adjustment.set_value(min(old, max(0, adjustment.get_upper() - adjustment.get_page_size()))))

        def rotate_screenshot(self):
            self.screenshot_rotation = (self.screenshot_rotation + 90) % 360
            self.show_screenshot()

        def show_screenshot(self):
            if self.screenshot_source is None: return
            self.image.set_from_pixbuf(self.screenshot_source.rotate_simple({
                0: GdkPixbuf.PixbufRotation.NONE, 90: GdkPixbuf.PixbufRotation.CLOCKWISE,
                180: GdkPixbuf.PixbufRotation.UPSIDEDOWN, 270: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE
            }[self.screenshot_rotation]))

        def work(self, function, callback):
            generation = self.generation
            future = self.executor.submit(function)
            def done(task):
                try:
                    result, failure = task.result(), None
                except Exception as exc:
                    result, failure = None, str(exc)
                def deliver():
                    if not self.closed:
                        callback(generation, result, failure)
                    return False
                GLib.idle_add(deliver)
            future.add_done_callback(done)

        def tick(self):
            if self.closed:
                return False
            if args.smoke_test or self.page in {"home", "providers", "sessions"} or self.polling:
                return True
            if self.page.startswith("slave"):
                slot = self.selected_slave or {}
                if not slot.get("host"):
                    return True
                function = lambda: self.client.screenshot(slot["host"], int(slot.get("port", 9527)))
            elif self.page == "activity":
                function = self.client.activity
            else:
                provider, sid = self.provider, self.llm_session
                function = lambda: self.client.llm("current", provider, sid)
            self.polling = True
            self.work(function, self.apply_update)
            return True

        def apply_update(self, generation, result, failure):
            result = result or {}
            if generation != self.generation:
                return
            self.polling = False
            if failure or not result.get("ok"):
                self.status.set_text(result.get("status_text", "连接中断" if failure else "失败") + " · 数据不可用（已有内容已过期）：" + (failure or str(result.get("error", "未知错误"))))
                if self.page == "codex":
                    self.stop_button.set_sensitive(False)
                return
            if self.page == "activity":
                if self.revision != result.get("revision"):
                    rows = [f"{d['address']}  {d['state']}" for d in result.get("connections", [])]
                    rows += ["", "最新记录（最多500条）"] + [
                        f"{time.strftime('%H:%M:%S', time.localtime(r['time']))}  {r['ip']}  {r['action']}  {r['state']}"
                        for r in reversed(result.get("records", []))]
                    adjustment = self.scroll.get_vadjustment()
                    old = adjustment.get_value()
                    self.activity_text.get_buffer().set_text("\n".join(rows))
                    GLib.idle_add(lambda: adjustment.set_value(old))
                    self.revision = result.get("revision")
                return
            if self.page == "codex":
                revision = (result.get("session_id"), result.get("revision"))
                if revision != self.revision:
                    adjustment = self.scroll.get_vadjustment()
                    old_position = adjustment.get_value()
                    follow = self.first_text or old_position + adjustment.get_page_size() >= adjustment.get_upper() - 24
                    self.conversation_text = result.get("text", "")
                    self.textview.get_buffer().set_text(compact_conversation(self.conversation_text, self.fold_expanded))
                    self.revision = revision
                    self.first_text = False
                    def restore():
                        if generation == self.generation and not self.closed:
                            maximum = max(0, adjustment.get_upper() - adjustment.get_page_size())
                            adjustment.set_value(maximum if follow else min(old_position, maximum))
                        return False
                    GLib.idle_add(restore)
                self.stop_button.set_sensitive(bool(result.get("busy")) and not result.get("stopped") and not self.action_pending)
                self.send_button.set_sensitive(not self.action_pending)
                message = result.get("status_text", f"Working（{result.get('working_seconds', 0)}s）" if result.get("busy") else "空闲")
                if not self.action_pending:
                    self.status.set_text(message)
            else:
                try:
                    raw = base64.b64decode(result["image_base64"], validate=True)
                    loader = GdkPixbuf.PixbufLoader()
                    loader.write(raw)
                    loader.close()
                    self.screenshot_source = loader.get_pixbuf()
                    self.show_screenshot()
                    self.status.set_text("截图采集完成 · " + time.strftime("%H:%M:%S") + " · 每秒请求，耗时超出时不堆积")
                except Exception as exc:
                    self.status.set_text("截图无效：" + str(exc))

        def action(self, kind):
            if self.action_pending:
                return
            text = self.input.get_text()
            if kind == "send" and (not text.strip() or len(text.encode("utf-8")) > 16384 or "\0" in text):
                self.action_status.set_text("请输入非空文字，最多16KB")
                return
            self.action_pending = True
            self.send_button.set_sensitive(False)
            self.stop_button.set_sensitive(False)
            self.action_status.set_text("正在提交；不会自动重发")
            request_id = "desktop-" + uuid.uuid4().hex
            provider, sid = self.provider, self.llm_session
            function = lambda: self.client.llm(kind, provider, sid, request_id=request_id, text=text)
            def completed(generation, result, failure):
                self.action_pending = False
                if self.page != "codex" or generation != self.generation:
                    return
                self.send_button.set_sensitive(True)
                if failure:
                    self.action_status.set_text("提交结果未确认，未重发：" + failure)
                else:
                    if "number" not in result:
                        self.action_status.set_text(str(result.get("error", "主机未返回提交状态")))
                        return
                    self.action_status.set_text(f"#{result['number']:03d} {result.get('message', result.get('error', ''))}")
                    if kind == "send" and result.get("ok") and generation == self.generation and self.input.get_text() == text:
                        self.input.set_text("")
            self.work(function, completed)

    window = ManagementWindow()
    if args.smoke_test:
        window.open_page(2)
        GLib.timeout_add(150, lambda: (window.destroy(), False)[1])
    Gtk.main()


if __name__ == "__main__":
    main()
