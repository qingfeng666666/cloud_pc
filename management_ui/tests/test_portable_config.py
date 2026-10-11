import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_pc.shared_components.common.portable_config import ConfigStore, program_directory


class PortableConfigTests(unittest.TestCase):
    def test_create_once_and_keep_custom_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'host.json'
            store = ConfigStore('host', path)
            self.assertEqual(store.load()['llm']['opencode']['url'], 'http://127.0.0.1:4096')
            store.update({'slaves': ['10.1.1.2:9527'], 'custom': 12})
            before = path.read_bytes()
            self.assertEqual(store.load()['slaves'], ['10.1.1.2:9527'])
            self.assertEqual(path.read_bytes(), before)
            store.update({'log_level': 'DEBUG'})
            self.assertEqual(store.load()['custom'], 12)

    def test_invalid_json_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'slave.json'
            path.write_text('{broken')
            with self.assertRaises(ValueError): ConfigStore('slave', path).load()
            self.assertEqual(path.read_text(), '{broken')

    def test_invalid_port_and_role(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore('slave', Path(directory) / 'slave.json')
            for port in (0, 65536, True, '9527'):
                with self.assertRaises(ValueError): store.update({'port': port})
            management = ConfigStore('management', Path(directory) / 'management.json')
            with self.assertRaises(ValueError): management.update({'role': 'remote'})

    def test_exe_directory_not_cwd_or_unpack_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(Path(directory)/'CloudHost.exe')):
                self.assertEqual(program_directory(), Path(directory))
            with patch.dict(os.environ, {'CLOUD_CONFIG_DIR': directory}):
                self.assertEqual(program_directory(), Path(directory))

    def test_only_three_settings_files_and_last_dialogue_shared(self):
        from cloud_pc.management_ui.recent_dialogue import RecentDialogue
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for role in ('host', 'slave', 'management'): ConfigStore(role, base/(role+'.json')).load()
            recent = RecentDialogue(base/'management.json')
            recent.save('dsh', 'session-1')
            ConfigStore('management', base/'management.json').update({'role': 'host'})
            self.assertEqual(recent.load()['session_id'], 'session-1')
            self.assertEqual(sorted(p.name for p in base.iterdir()), ['host.json', 'management.json', 'slave.json'])
