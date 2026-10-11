import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'cloud_pc/shared_components'))
sys.path.insert(0, str(ROOT / 'cloud_pc/desktop_version'))
from common.portable_config import ConfigStore
from host.api_server import HostAPI


class SlaveSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_remove_persists_without_touching_llm(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore('host', Path(directory) / 'host.json')
            original = store.load()['llm']
            api = HostAPI(config_store=store, management_state=Path(directory) / 'owner.json')
            with patch('host.connection.SlaveClient.manual_connect', new_callable=AsyncMock), \
                    patch('host.connection.SlaveClient.stop', new_callable=AsyncMock), \
                    patch('cloud_pc.host_program.android_current.AndroidCurrentChannel.run', new_callable=AsyncMock):
                result = await api.slave_settings({'action': 'add', 'host': '192.0.2.1', 'port': 9527})
                self.assertEqual(len(result['slaves']), 1)
                self.assertEqual(store.load()['slaves'], ['192.0.2.1:9527'])
                await api.slave_settings({'action': 'add', 'host': '192.0.2.1'})
                self.assertEqual(len(api.slave_list()['slaves']), 1)
                api.management.owner = {'address': '192.0.2.1:9527'}
                with self.assertRaisesRegex(ValueError, '不能移除'):
                    await api.slave_settings({'action': 'remove', 'host': '192.0.2.1'})
                api.management.owner = None
                await api.slave_settings({'action': 'remove', 'host': '192.0.2.1'})
                self.assertEqual(store.load()['slaves'], [])
                self.assertEqual(api.slave_list()['slaves'], [])
                self.assertEqual(store.load()['llm'], original)
                await api.close()

    async def test_invalid_input_does_not_save(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore('host', Path(directory) / 'host.json'); store.load()
            before = store.path.read_bytes()
            api = HostAPI(config_store=store, management_state=Path(directory) / 'owner.json')
            for host, port in [('bad ip', 9527), ('192.0.2.1', 0), ('192.0.2.1', True)]:
                with self.assertRaises(ValueError): await api.slave_settings({'action': 'add', 'host': host, 'port': port})
            self.assertEqual(store.path.read_bytes(), before)
            await api.close()
