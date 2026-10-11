import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cloud_pc/shared_components"))
from cloud_pc.host_program.android_current import AndroidCurrentChannel
from common.protocol import TYPE_MANAGEMENT_REQUEST


def event(number, operation):
    return {"send_id": f"{number:032x}", "operation": operation,
            "payload": {"_management_session": "a" * 32}}


class ChannelTests(unittest.IsolatedAsyncioTestCase):
    def channel(self):
        client = SimpleNamespace(slave_id="phone", address="10.0.0.7:9527", host_ip="10.0.0.7")
        api = SimpleNamespace(request=AsyncMock(return_value={"ok": True}))
        return AndroidCurrentChannel(api, client, None), api, client

    async def finish(self, channel):
        await asyncio.gather(*list(channel._tasks))
        await asyncio.sleep(0)

    async def test_idle_channel_subscribes_without_polling(self):
        channel, api, client = self.channel()
        subscribed, removed = [], []
        def subscribe(kind, callback):
            subscribed.append(kind)
            return lambda: removed.append(kind)
        client.subscribe = subscribe
        task = asyncio.create_task(channel.run())
        await asyncio.sleep(.02)
        api.request.assert_not_called()
        self.assertEqual(subscribed, [TYPE_MANAGEMENT_REQUEST])
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(removed, subscribed)

    async def test_slow_refresh_does_not_block_lists(self):
        channel, _, _ = self.channel()
        started, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def management(message):
            if message["operation"] == "llm_current":
                started.set()
                await release.wait()
            else:
                completed.set()
        channel.management = management
        await channel.receive_request(event(1, "llm_current"))
        await started.wait()
        await channel.receive_request(event(2, "llm_sessions"))
        await asyncio.wait_for(completed.wait(), .2)
        release.set()
        await self.finish(channel)

    async def test_mutations_ordered_and_duplicate_ignored(self):
        channel, _, _ = self.channel()
        calls = []
        async def management(message):
            calls.append(message["operation"] + " start")
            await asyncio.sleep(.01)
            calls.append(message["operation"] + " end")
        channel.management = management
        await channel.receive_request(event(1, "llm_send"))
        await channel.receive_request(event(1, "llm_send"))
        await channel.receive_request(event(2, "llm_stop"))
        await self.finish(channel)
        self.assertEqual(calls, ["llm_send start", "llm_send end", "llm_stop start", "llm_stop end"])

    async def test_waiting_refresh_superseded_by_navigation(self):
        channel, api, _ = self.channel()
        called = []
        async def management(message): called.append(message["operation"])
        channel.management = management
        await channel._background_reads.acquire()
        await channel._background_reads.acquire()
        await channel.receive_request(event(1, "llm_current"))
        await asyncio.sleep(0)
        await channel.receive_request(event(2, "llm_providers"))
        channel._background_reads.release()
        channel._background_reads.release()
        await self.finish(channel)
        self.assertEqual(called, ["llm_providers"])
        self.assertEqual(api.request.call_args.args[2]["result"]["code"], "superseded")

    async def test_shutdown_cleans_pending_tasks(self):
        channel, _, client = self.channel()
        client.subscribe = lambda *_: lambda: None
        async def management(message): await asyncio.sleep(10)
        channel.management = management
        task = asyncio.create_task(channel.run())
        await asyncio.sleep(0)
        await channel.receive_request(event(1, "llm_current"))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(channel._tasks)
