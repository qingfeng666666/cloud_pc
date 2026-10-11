import unittest
from unittest.mock import patch, MagicMock

from cloud_pc.host_program.llm_backends import OpenCodeBackend


class StartupTests(unittest.TestCase):
    def backend(self):
        return OpenCodeBackend({'url': 'http://127.0.0.1:4096',
                                'start_command': ['opencode', 'serve', '--port', '4096']})

    def test_existing_service_not_started(self):
        with patch('socket.create_connection'), patch('subprocess.Popen') as launch:
            self.backend().ensure_ready()
            launch.assert_not_called()

    def test_missing_service_started_once(self):
        with patch('socket.create_connection', side_effect=[ConnectionRefusedError(), MagicMock(), MagicMock()]), \
                patch('subprocess.Popen') as launch:
            backend = self.backend()
            backend.ensure_ready()
            backend.ensure_ready()
            self.assertEqual(launch.call_count, 1)

    def test_other_network_failure_does_not_launch(self):
        with patch('socket.create_connection', side_effect=TimeoutError()), patch('subprocess.Popen') as launch:
            with self.assertRaises(TimeoutError): self.backend().ensure_ready()
            launch.assert_not_called()
