import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_pc.management_ui.client import HostClient
from cloud_pc.shared_components.common.portable_config import ConfigStore
from cloud_pc.shared_components.slave.management_bridge import ManagementBridge


class HostIPTests(unittest.TestCase):
    def test_config_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as root:
            store = ConfigStore('management', Path(root) / 'management.json')
            self.assertEqual(store.load()['host_ip'], '10.0.0.2')
            store.update({'host_ip': '192.168.4.10'})
            self.assertEqual(store.load()['host_ip'], '192.168.4.10')
            with self.assertRaises(ValueError): store.update({'host_ip': 'http://example.com'})
            self.assertEqual(store.load()['host_ip'], '192.168.4.10')

    def test_activation_stays_local(self):
        client = HostClient('http://127.0.0.1:19528', '192.168.4.10')
        with patch.object(client, '_request', return_value={'ok': True}) as request:
            client.activate(True)
            self.assertEqual(request.call_args.args[1]['host_ip'], '192.168.4.10')
            client.release()
            self.assertEqual(request.call_args.args[1]['host_ip'], '192.168.4.10')
        self.assertEqual(client.base_url, 'http://127.0.0.1:19528')

    def test_bridge_uses_target_and_rejects_invalid_ip(self):
        bridge = ManagementBridge()
        with patch('socket.create_connection', side_effect=OSError('test')) as connect:
            bridge.register('activate', {'host_ip': '192.168.4.10'})
            self.assertEqual(connect.call_args.args[0], ('192.168.4.10', 19526))
            connect.reset_mock()
            self.assertFalse(bridge.register('activate', {'host_ip': 'bad'})['ok'])
            connect.assert_not_called()
