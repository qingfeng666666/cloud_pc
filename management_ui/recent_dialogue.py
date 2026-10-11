"""Persist only the last successfully opened target, never messages or secrets."""
import json
import re
from cloud_pc.shared_components.common.portable_config import ConfigStore


class RecentDialogue:
    def __init__(self, path=None):
        self.store = ConfigStore('management', path)
        self.path = self.store.path

    @staticmethod
    def valid(value):
        return (isinstance(value, dict) and value.get('provider') in {'codex', 'dsh', 'opencode'}
                and isinstance(value.get('session_id'), str)
                and re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', value['session_id']))

    def load(self):
        try:
            value = self.store.load().get('last_dialogue')
            return value if self.valid(value) else None
        except (OSError, ValueError): return None

    def save(self, provider, session_id):
        value = {'provider': provider, 'session_id': session_id}
        if not self.valid(value): raise ValueError('无效会话目标')
        self.store.update({'last_dialogue': value})
