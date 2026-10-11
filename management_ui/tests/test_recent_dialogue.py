import json
import tempfile
import unittest
from pathlib import Path

from cloud_pc.management_ui.recent_dialogue import RecentDialogue


class RecentDialogueTests(unittest.TestCase):
    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'last.json'
            recent = RecentDialogue(path)
            self.assertIsNone(recent.load())
            recent.save('codex', 'thread-1')
            self.assertEqual(RecentDialogue(path).load(), {'provider': 'codex', 'session_id': 'thread-1'})
            recent.save('opencode', 'session-2')
            self.assertEqual(recent.load()['session_id'], 'session-2')
            self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_invalid_or_corrupt_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'last.json'
            recent = RecentDialogue(path)
            for body in ['broken', '{}', json.dumps({'provider': 'other', 'session_id': 'x'}), 'x' * 2049]:
                path.write_text(body)
                self.assertIsNone(recent.load())
            with self.assertRaises(ValueError): recent.save('codex', '../bad')
