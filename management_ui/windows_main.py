"""Independent Windows management client (Tk, no slave networking)."""
import base64
import io
import json
import queue
import uuid
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from cloud_pc.management_ui.client import HostClient
from cloud_pc.management_ui.recent_dialogue import RecentDialogue
from cloud_pc.management_ui.conversation_preview import compact_conversation
from cloud_pc.shared_components.common.portable_config import ConfigStore
from cloud_pc.shared_components.common.release import __version__


class Window:
    def __init__(self, root):
        self.root = root
        root.title("云电脑管理界面 " + __version__)
        root.geometry("900x700")
        self.store = ConfigStore('management')
        self.config_path = self.store.path
        settings = self.store.load()
        self.config = {'role': settings['role'], 'host_ip': settings['host_ip']}
        self.selected_slave = None
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.results = queue.Queue()
        self.generation = 0
        self.page = -1
        self.pending = False
        self.polling = False
        self.revision = None
        self.provider = ""
        self.llm_session = ""
        self.recent = RecentDialogue(self.config_path)
        self.session_rows = []
        self.next_cursor = None
        self.closed = False
        self._client = None
        self.frame = ttk.Frame(root, padding=16)
        self.frame.pack(fill="both", expand=True)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.home()
        self.pool.submit(self.client().activate)
        self.drain()
        self.tick()

    def close(self):
        self.closed = True
        if self._client: self.pool.submit(self._client.release)
        self.pool.shutdown(wait=False)
        self.root.destroy()

    def client(self):
        url = "http://127.0.0.1:" + ("19527" if self.config["role"] == "host" else "19528")
        if self._client is None or self._client.base_url != url or self._client.host_ip != self.config['host_ip']:
            if self._client: self.pool.submit(self._client.release)
            self._client = HostClient(url, self.config['host_ip'])
        return self._client

    def reset(self, page):
        self.generation += 1
        self.page = page
        self.polling = False
        self.revision = None
        for widget in self.frame.winfo_children():
            widget.destroy()

    def home(self):
        self.reset(-1)
        ttk.Button(self.frame, text="添加从机", command=self.add_slave).pack(fill="x", pady=6)
        ttk.Button(self.frame, text="刷新从机列表", command=self.load_slaves).pack(fill="x", pady=6)
        self.slave_rows = ttk.Frame(self.frame); self.slave_rows.pack(fill="both", expand=True)
        self.status = ttk.Label(self.frame, text="正在从主机读取从机列表…"); self.status.pack(fill="x")
        for index, name in ((2, "LLM 对话界面"), (3, "主机运行状态")):
            ttk.Button(self.frame, text=name, command=lambda i=index: self.open(i)).pack(fill="x", pady=12, ipady=12)
        # Settings via application menu, not another home entry.
        menu = tk.Menu(self.root)
        menu.add_command(label="连接设置", command=self.settings)
        menu.add_command(label="激活本管理界面", command=lambda: self.pool.submit(self.client().activate, True))
        self.root.config(menu=menu)
        ttk.Frame(self.frame, height=80).pack(side="bottom", fill="x")
        self.load_slaves()

    def load_slaves(self):
        if self.page == -1: self.work(self.client().slaves, "slaves")

    def add_slave(self):
        host = simpledialog.askstring("添加从机", "从机 IPv4 地址", parent=self.root)
        if not host: return
        port = simpledialog.askinteger("添加从机", "端口", initialvalue=9527, minvalue=1, maxvalue=65535, parent=self.root)
        if port: self.work(lambda: self.client().slaves("add", host=host.strip(), port=port), "slaves")

    def remove_slave(self, item):
        if messagebox.askyesno("移除从机", "从主机配置中移除 " + item['address'] + "？不会删除文件。"):
            self.work(lambda: self.client().slaves("remove", host=item['host'], port=item['port']), "slaves")

    def open_slave(self, item):
        self.selected_slave = item
        self.render(0)

    def settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("连接设置（只访问本机程序）")
        ttk.Label(dialog, text="管理界面所在设备").pack(anchor="w", padx=12)
        role = ttk.Combobox(dialog, state="readonly", values=["从机设备（本机从机19528）", "主机设备（本机主机19527）"], width=55)
        role.current(1 if self.config["role"] == "host" else 0)
        role.pack(padx=12, pady=5)
        ttk.Label(dialog, text="主机 IP（由本机从机发起注册）").pack(anchor="w", padx=12)
        host_ip = ttk.Entry(dialog, width=40)
        host_ip.insert(0, self.config['host_ip'])
        host_ip.pack(padx=12, pady=5)
        def save():
            try:
                value = self.store.update({'role': 'host' if role.current() == 1 else 'slave', 'host_ip': host_ip.get().strip()})
                self.config = {'role': value['role'], 'host_ip': value['host_ip']}
                dialog.destroy()
                self.home()
                self.pool.submit(self.client().activate, True)
            except Exception as exc:
                messagebox.showerror("配置错误", str(exc))
        ttk.Label(dialog, text="配置文件：" + str(self.config_path), wraplength=550).pack(padx=12)
        ttk.Button(dialog, text="保存并连接主机", command=save).pack(pady=12)

    def open(self, page):
        if page == 2:
            self.providers()
            return
        self.render(page)

    def providers(self):
        self.reset(4)
        ttk.Button(self.frame, text="返回首页", command=self.home).pack(anchor="w")
        ttk.Label(self.frame, text="LLM 对话界面 · 模型与认证由用户预先在主机配置").pack(pady=12)
        previous = self.recent.load()
        if previous:
            ttk.Button(self.frame, text="返回上次对话 · " + previous['provider'],
                       command=lambda: self.resume_previous(previous)).pack(fill="x", pady=6)
        self.choices = ttk.Frame(self.frame); self.choices.pack(fill="both", expand=True)
        self.status = ttk.Label(self.frame, text="读取主机配置…"); self.status.pack(fill="x")
        self.work(lambda: self.client().llm("providers"), "providers")

    def resume_previous(self, previous):
        self.provider = previous['provider']
        provider, sid = self.provider, previous['session_id']
        self.status.config(text="正在打开上次对话…")
        self.work(lambda: self.client().llm("open", provider, sid), "open")

    def sessions(self, provider):
        self.provider = provider
        self.llm_session = ""
        self.session_rows = []
        self.reset(5)
        ttk.Button(self.frame, text="返回 LLM 列表", command=self.providers).pack(anchor="w")
        ttk.Label(self.frame, text=provider + " · 选择要继续的会话").pack(pady=8)
        row = ttk.Frame(self.frame); row.pack(fill="x")
        ttk.Button(row, text="新建会话", command=self.new_session).pack(side="left")
        ttk.Button(row, text="刷新列表", command=lambda: self.sessions(provider)).pack(side="left", padx=8)
        area = ttk.Frame(self.frame); area.pack(fill="both", expand=True, pady=8)
        self.listbox = tk.Listbox(area, font=("", 12)); self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(area, command=self.listbox.yview); scrollbar.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)
        self.listbox.bind("<Double-Button-1>", lambda _: self.choose_session())
        ttk.Button(self.frame, text="继续选中会话", command=self.choose_session).pack(fill="x")
        self.more = ttk.Button(self.frame, text="更多会话", command=self.more_sessions, state="disabled"); self.more.pack(fill="x")
        self.status = ttk.Label(self.frame, text="读取会话…"); self.status.pack(fill="x")
        self.work(lambda: self.client().llm("sessions", provider), "sessions")

    def more_sessions(self):
        cursor, provider = self.next_cursor, self.provider
        if cursor:
            self.more.config(state="disabled")
            self.work(lambda: self.client().llm("sessions", provider, cursor=cursor), "sessions")

    def new_session(self):
        if self.pending: return
        self.pending = True
        provider = self.provider
        rid = "win-new-" + uuid.uuid4().hex
        self.work(lambda: self.client().llm("create", provider, request_id=rid), "create")

    def choose_session(self):
        selected = self.listbox.curselection()
        if not selected: return
        sid, provider = self.session_rows[selected[0]]["id"], self.provider
        self.work(lambda: self.client().llm("open", provider, sid), "open")

    def render(self, page):
        self.reset(page)
        self.conversation_text = ""
        self.fold_expanded = False
        self.screenshot_rotation = 0
        self.screenshot_source = None
        ttk.Button(self.frame, text="返回首页", command=self.home).pack(anchor="w")
        if page in (2, 3):
            if page == 2:
                ttk.Button(self.frame, text=self.provider + " · 切换会话", command=lambda: self.sessions(self.provider)).pack(anchor="w")
                self.codex_controls()
            self.text_area()
        else:
            ttk.Button(self.frame, text="旋转截图 ↻ 90°", command=self.rotate_screenshot).pack(anchor="w")
            self.image = ttk.Label(self.frame, anchor="center")
            self.image.pack(fill="both", expand=True)
        self.status = ttk.Label(self.frame, text="等待更新", wraplength=850)
        self.status.pack(fill="x")
        if page == 2:
            self.action_status = ttk.Label(self.frame, text="", wraplength=850)
            self.action_status.pack(fill="x")
        else:
            ttk.Frame(self.frame, height=60).pack(fill="x")
        self.poll()

    def codex_controls(self):
            self.entry = ttk.Entry(self.frame)
            self.entry.pack(fill="x", pady=10, ipady=6)
            row = ttk.Frame(self.frame)
            row.pack(fill="x")
            self.send = ttk.Button(row, text="SEND", command=lambda: self.action("send"))
            self.send.pack(side="left")
            self.stop = ttk.Button(row, text="STOP", command=lambda: self.action("stop"), state="disabled")
            self.stop.pack(side="left", padx=12)
            self.fold_button = ttk.Button(row, text="展开全部", command=self.toggle_conversation)
            self.fold_button.pack(side="left", padx=12)

    def toggle_conversation(self):
        self.fold_expanded = not self.fold_expanded
        self.fold_button.config(text="收起长内容" if self.fold_expanded else "展开全部")
        top = self.text.index("@0,0")
        self.text.config(state="normal"); self.text.delete("1.0", "end")
        self.text.insert("end", compact_conversation(self.conversation_text, self.fold_expanded))
        self.text.config(state="disabled"); self.text.yview(top)

    def rotate_screenshot(self):
        self.screenshot_rotation = (self.screenshot_rotation + 90) % 360
        self.show_screenshot()

    def show_screenshot(self):
        if self.screenshot_source is None: return
        from PIL import ImageTk
        image = self.screenshot_source.rotate(-self.screenshot_rotation, expand=True)
        image.thumbnail((850, 500))
        self.photo = ImageTk.PhotoImage(image)
        self.image.config(image=self.photo)
    def text_area(self):
            area = ttk.Frame(self.frame)
            area.pack(fill="both", expand=True, pady=10)
            self.text = tk.Text(area, wrap="word", state="disabled")
            scroll = ttk.Scrollbar(area, command=self.text.yview)
            self.text.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            self.text.pack(fill="both", expand=True)

    def work(self, function, kind, text=""):
        generation = self.generation
        def run():
            try:
                result, error = function(), None
            except Exception as exc:
                result, error = {}, str(exc)
            self.results.put((generation, kind, text, result, error))
        self.pool.submit(run)

    def poll(self):
        if self.page < 0 or self.page >= 4 or self.polling:
            return
        client = self.client()
        if self.page == 3:
            self.polling = True
            self.work(client.activity, "poll")
            return
        if self.page != 2 and not self.selected_slave:
            self.status.config(text="请返回首页选择从机")
            return
        self.polling = True
        provider, sid = self.provider, self.llm_session
        function = lambda: client.llm("current", provider, sid)
        # Capture target before background thread starts.
        if self.page != 2:
            target = self.selected_slave['host']
            port = self.selected_slave['port']
            function = lambda: client.screenshot(target, port)
        self.work(function, "poll")

    def tick(self):
        self.poll()
        self.root.after(1000, self.tick)

    def action(self, kind):
        if self.pending:
            return
        text = self.entry.get()
        if kind == "send" and (not text.strip() or len(text.encode()) > 16384 or "\0" in text):
            self.action_status.config(text="请输入非空文字，最多16KB")
            return
        self.pending = True
        self.send.config(state="disabled")
        self.stop.config(state="disabled")
        self.action_status.config(text="正在提交，不会自动重发")
        request_id = "win-" + uuid.uuid4().hex
        client = self.client()
        provider, sid = self.provider, self.llm_session
        self.work(lambda: client.llm(kind, provider, sid, request_id=request_id, text=text), kind, text)

    def drain(self):
        while not self.results.empty():
            generation, kind, text, result, error = self.results.get()
            if kind != "poll":
                self.pending = False
            if generation != self.generation:
                continue
            if kind == "slaves":
                if error or not result.get("ok"):
                    self.status.config(text=error or result.get("error", "读取失败")); continue
                for widget in self.slave_rows.winfo_children(): widget.destroy()
                for item in result.get("slaves", []):
                    row = ttk.Frame(self.slave_rows); row.pack(fill="x", pady=5)
                    title = f"{item.get('name', '')} · {item['address']} · " + ("在线" if item.get('connected') else "离线")
                    ttk.Button(row, text=title, command=lambda s=item: self.open_slave(s)).pack(side="left", fill="x", expand=True)
                    ttk.Button(row, text="移除", command=lambda s=item: self.remove_slave(s)).pack(side="right")
                self.status.config(text="列表来自主机 · 添加／移除保存到主机 host.json")
                continue
            if kind in {"providers", "sessions", "create", "open"}:
                if error or not result.get("ok"):
                    self.status.config(text=error or result.get("error", "API 失败")); continue
                if kind == "providers":
                    for item in result.get("providers", []):
                        name = item["id"]
                        ttk.Button(self.choices, text=name + ("" if item.get("enabled") else "（主机未启用）"),
                            command=lambda p=name: self.sessions(p), state="normal" if item.get("enabled") else "disabled").pack(fill="x", pady=8, ipady=12)
                    self.status.config(text="请选择 LLM")
                elif kind == "sessions":
                    for row in result.get("sessions", []):
                        self.session_rows.append(row)
                        title = ' '.join(row.get("title", row["id"]).split())
                        self.listbox.insert("end", title[:160] + ('…' if len(title) > 160 else '') + "  [" + row["id"] + "]")
                    self.next_cursor = result.get("next_cursor")
                    self.more.config(state="normal" if self.next_cursor else "disabled")
                    self.status.config(text="选择会话，或新建会话")
                elif kind == "create":
                    sid, provider = result["session_id"], self.provider
                    self.work(lambda: self.client().llm("open", provider, sid), "open")
                else:
                    self.provider = result.get("provider", self.provider)
                    self.llm_session = result["session_id"]
                    try: self.recent.save(self.provider, self.llm_session)
                    except OSError: pass  # History failure must not block an opened session.
                    self.render(2)
                continue
            if kind == "poll":
                self.polling = False
            if error or not result.get("ok"):
                target = self.action_status if self.page == 2 and kind != "poll" else self.status
                target.config(text=result.get("status_text", "连接中断" if error else "失败") + " · 结果未确认/旧内容已过期：" + (error or result.get("error") or result.get("message", "未知错误")))
                if self.page == 2:
                    self.stop.config(state="disabled")
                    self.send.config(state="disabled" if self.pending else "normal")
                continue
            if kind != "poll":
                self.send.config(state="normal")
                self.action_status.config(text=f"#{result.get('number', '?')} {result.get('message', '')}")
                if kind == "send" and self.entry.get() == text:
                    self.entry.delete(0, "end")
            elif self.page == 3:
                if result.get("revision") != self.revision:
                    rows = [f"{d['address']}  {d['state']}" for d in result.get("connections", [])]
                    rows += ["", "最新记录（最多500条）"] + [
                        f"{time.strftime('%H:%M:%S', time.localtime(r['time']))}  {r['ip']}  {r['action']}  {r['state']}"
                        for r in reversed(result.get("records", []))]
                    top = self.text.index("@0,0")
                    self.text.config(state="normal"); self.text.delete("1.0", "end")
                    self.text.insert("end", "\n".join(rows)); self.text.config(state="disabled")
                    self.text.yview(top); self.revision = result.get("revision")
                self.status.config(text="每秒更新 · 最新记录在上")
            elif self.page == 2:
                revision = (result.get("session_id"), result.get("revision"))
                if revision != self.revision:
                    first, last = self.text.yview()
                    old_top = self.text.index("@0,0")
                    follow = self.revision is None or last >= .99
                    self.text.config(state="normal")
                    self.text.delete("1.0", "end")
                    self.conversation_text = result.get("text", "")
                    self.text.insert("end", compact_conversation(self.conversation_text, self.fold_expanded))
                    self.text.config(state="disabled")
                    if follow:
                        self.text.yview_moveto(1)
                    else:
                        self.text.yview(old_top)
                    self.revision = revision
                self.send.config(state="disabled" if self.pending else "normal")
                self.stop.config(state="normal" if result.get("busy") and not result.get("stopped") and not self.pending else "disabled")
                if not self.pending:
                    self.status.config(text=result.get("status_text", f"Working（{result.get('working_seconds', 0)}s）" if result.get("busy") else "空闲"))
            else:
                try:
                    from PIL import Image, ImageTk
                    image = Image.open(io.BytesIO(base64.b64decode(result["image_base64"], validate=True)))
                    self.screenshot_source = image.copy()
                    self.show_screenshot()
                    self.status.config(text="截图已更新 · 每秒请求")
                except Exception as exc:
                    self.status.config(text="截图无法显示：" + str(exc))
        self.root.after(100, self.drain)


def main():
    root = tk.Tk()
    try: Window(root)
    except (ValueError, OSError) as error:
        messagebox.showerror('配置读取失败（未覆盖文件）', str(error), parent=root)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
