"""Bounded metadata-only host activity feed; never stores command arguments."""
import logging
import time
import uuid
from collections import deque


class ActivityFeed:
    def __init__(self):
        self.records = deque(maxlen=500)
        self.active = {}
        self.revision = 0

    def record(self, ip, action, state, operation_id=None):
        self.revision += 1
        item = {"seq": self.revision, "time": time.time(), "ip": ip,
                "action": action, "state": state, "id": operation_id or uuid.uuid4().hex}
        self.records.append(item)
        if state == "进行中":
            self.active[item["id"]] = item
        else:
            self.active.pop(item["id"], None)
        logging.getLogger("cloud.host.activity").info("%s %s %s", ip, action, state)
        return item["id"]

    def snapshot(self, clients):
        return {"ok": True, "revision": self.revision, "records": list(self.records),
                "active": list(self.active.values()),
                "connections": [{"ip": c.host_ip, "address": c.address, "state": c.state}
                                for c in clients]}
