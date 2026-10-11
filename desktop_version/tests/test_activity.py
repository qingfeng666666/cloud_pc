import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from cloud_pc.host_program.activity import ActivityFeed


class ActivityTests(unittest.TestCase):
    def test_bounded_and_active(self):
        feed = ActivityFeed()
        identifier = feed.record("10.0.0.7", "下载文件", "进行中")
        self.assertEqual(len(feed.snapshot([])["active"]), 1)
        feed.record("10.0.0.7", "下载文件", "完成", identifier)
        self.assertFalse(feed.snapshot([])["active"])
        for i in range(510):
            feed.record("10.0.0.7", "连接从机", "已连接")
        self.assertEqual(len(feed.snapshot([])["records"]), 500)


class DownloadActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_paths_record_completion_and_failure(self):
        from host.api_server import HostAPI
        api = HostAPI()
        api._clients["win"] = SimpleNamespace(host_ip="192.168.4.20")
        for result, state in [({"text": "private content", "size": 15}, "完成"),
                              ({"error": "Download timeout"}, "超时")]:
            api._download_file_impl = AsyncMock(return_value=result)
            await api._download_file("win", "G:/eu5_project/cloud_pc/ar_click_code.txt")
            snapshot = api.activity.snapshot([])
            self.assertEqual(snapshot["records"][-1]["state"], state)
            self.assertIn("ar_click_code.txt", snapshot["records"][-1]["action"])
            self.assertNotIn("private content", str(snapshot))
            self.assertFalse(snapshot["active"])
