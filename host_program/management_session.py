"""One active management UI and host-owned recovery of its slave connection."""
import asyncio
import ipaddress
import json
import logging
import re
import struct
import time
from collections import deque
from pathlib import Path

LOG = logging.getLogger("cloud.host.management")
REGISTER, REGISTER_RESULT = 0x0701, 0x0781


class ManagementSessions:
    def __init__(self, api, interval=5, state_path=None):
        self.api = api
        if interval <= 0: raise ValueError("Management interval must be positive")
        self.interval = interval
        self.state_path = Path(state_path) if state_path else None
        self.owner = None
        self.retired = deque(maxlen=1024)
        self.server = None
        self.watch_task = None
        if self.state_path and self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                owner = state.get("owner")
                if isinstance(owner,dict) and isinstance(owner.get("address"),str) and re.fullmatch(r"[a-f0-9]{32}",owner.get("session_id","")):
                    self.owner = owner
                self.retired.extend(state.get("retired",[])[:1024])
            except (OSError, ValueError, TypeError):
                LOG.exception("Cannot restore management session")

    def save(self):
        if self.state_path:
            self.state_path.parent.mkdir(parents=True,exist_ok=True)
            temporary = self.state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"owner":self.owner,"retired":list(self.retired)}),encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(self.state_path)

    def client(self):
        if not self.owner or self.owner["address"] == "host": return None
        return next((c for c in self.api._clients.values() if c.address==self.owner["address"]),None)

    def apply_policy(self):
        required = self.client()
        for c in self.api._clients.values():
            c.set_management_required(c is required,self.interval)

    def authorize(self, session_id, address):
        if not self.owner or self.owner["session_id"]!=session_id or self.owner["address"]!=address:
            return {"ok":False,"code":"management_inactive","error":"本管理界面未激活或已被其他界面接替"}
        return None

    async def activate(self, session_id, address):
        if not isinstance(session_id,str) or not re.fullmatch(r"[a-f0-9]{32}",session_id):
            return {"ok":False,"error":"Invalid management session"}
        if session_id in self.retired:
            return {"ok":False,"code":"management_inactive","error":"旧管理会话已失效，请手动重新激活"}
        if address != "host" and not any(c.address==address for c in self.api._clients.values()):
            return {"ok":False,"error":"管理从机尚未配置"}
        if self.owner and self.owner["session_id"]==session_id and self.owner["address"]!=address:
            return {"ok":False,"error":"管理会话不属于此设备"}
        if not self.owner or self.owner["session_id"]!=session_id:
            previous = self.owner
            if previous: self.retired.append(previous["session_id"])
            self.owner = {"session_id":session_id,"address":address,"activated_at":time.time()}
            try: self.save()
            except OSError:
                self.owner = previous
                if previous: self.retired.pop()
                raise
            self.api.activity.record(address,"激活管理界面","已接替旧界面" if previous else "已激活")
        self.apply_policy()
        c = self.client()
        if c and c.state!="online": await c.manual_connect()
        return {"ok":True,"active":True,"reconnect_interval":self.interval}

    def release(self, session_id, address):
        denied = self.authorize(session_id,address)
        if denied: return denied
        previous = self.owner
        self.retired.append(session_id);self.owner=None
        try: self.save()
        except OSError:
            self.owner=previous;self.retired.pop();raise
        self.apply_policy()
        self.api.activity.record(address,"关闭管理界面","取消持续重连")
        return {"ok":True,"active":False}

    def status(self):
        # Do not expose the session bearer token in health/activity diagnostics.
        return {"active":bool(self.owner),"address":self.owner["address"] if self.owner else None,
                "reconnect_interval":self.interval}

    async def watch(self):
        while True:
            try:
                self.apply_policy()
                c = self.client()
                if c and c.state!="online": await c.manual_connect()
            except Exception: LOG.exception("Management connection recovery")
            await asyncio.sleep(self.interval)

    async def start(self, bind=None, port=19526):
        self.apply_policy()
        if bind is not None:
            self.server=await asyncio.start_server(self.bootstrap,bind,port,limit=4096)
            LOG.info("Management CLDC registration listener %s:%s",bind,port)
        self.watch_task=asyncio.create_task(self.watch())

    async def close(self):
        if self.watch_task:
            self.watch_task.cancel()
            await asyncio.gather(self.watch_task,return_exceptions=True)
        if self.server:
            self.server.close();await self.server.wait_closed()

    async def bootstrap(self, reader, writer):
        try:
            peer = str(ipaddress.ip_address(writer.get_extra_info("peername")[0]))
            candidates = [c for c in self.api._clients.values() if c.host_ip==peer]
            if not candidates: return  # Only already-configured slaves may register.
            h = await asyncio.wait_for(reader.readexactly(12),5)
            magic,version,kind,length=struct.unpack("!4sHHI",h)
            if magic!=b"CLDC" or version!=1 or kind!=REGISTER or not 0<length<=4096: return
            body=json.loads(await asyncio.wait_for(reader.readexactly(length),5))
            if not isinstance(body,dict): return
            targets=[c for c in candidates if c.port==body.get("slave_port",9527)]
            if len(targets)!=1: result={"ok":False,"error":"管理从机端口未配置或不唯一"}
            elif body.get("operation")=="activate":
                result=await self.activate(body.get("session_id"),targets[0].address)
            elif body.get("operation")=="release":
                result=self.release(body.get("session_id"),targets[0].address)
            else: result={"ok":False,"error":"注册端口不接受业务或转发请求"}
            data=json.dumps(result,ensure_ascii=False).encode()
            writer.write(struct.pack("!4sHHI",b"CLDC",1,REGISTER_RESULT,len(data))+data)
            await asyncio.wait_for(writer.drain(),5)
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            LOG.warning("Management registration rejected")
        finally:
            writer.close()
            try: await writer.wait_closed()
            except OSError: pass
