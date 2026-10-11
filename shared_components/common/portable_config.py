"""Three portable JSON settings files; frozen apps use the EXE directory."""
import copy
import ipaddress
import json
import os
import socket
import sys
import uuid
from pathlib import Path


LLM_DEFAULTS = {
    'codex': {'enabled': True, 'socket': 'auto', 'command': ['codex', 'app-server', '--stdio']},
    'dsh': {'enabled': True, 'url': 'http://127.0.0.1:3080', 'start_command': ['dsh', 'web']},
    'opencode': {'enabled': True, 'url': 'http://127.0.0.1:4096',
                 'start_command': ['opencode', 'serve', '--hostname', '127.0.0.1', '--port', '4096']}}
DEFAULTS = {
    'host': {'headless': True, 'slaves': [], 'current_slaves': [], 'relay_grants': [],
             'management_bind': '0.0.0.0', 'management_port': 19526, 'management_interval': 5,
             'log_level': 'INFO', 'llm': LLM_DEFAULTS},
    'slave': {'root': 'shared', 'port': 9527, 'name': socket.gethostname(), 'log_level': 'INFO'},
    'management': {'role': 'slave', 'host_ip': '10.0.0.2', 'last_dialogue': None}}


def program_directory():
    if getattr(sys, 'frozen', False): return Path(sys.executable).resolve().parent
    if os.environ.get('CLOUD_CONFIG_DIR'): return Path(os.environ['CLOUD_CONFIG_DIR']).resolve()
    return Path(__file__).resolve().parents[2]


class ConfigStore:
    def __init__(self, kind, path=None):
        self.kind = kind
        self.path = Path(path).resolve() if path else program_directory() / (kind + '.json')

    def load(self):
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self.path.open('x', encoding='utf-8') as output:
                    if os.name != 'nt': self.path.chmod(0o600)
                    output.write(json.dumps(DEFAULTS[self.kind], ensure_ascii=False, indent=2) + '\n')
            except FileExistsError: pass
        try:
            data = json.loads(self.path.read_text(encoding='utf-8-sig'))
        except (ValueError, OSError) as error:
            raise ValueError('无法读取配置 ' + str(self.path) + '：' + str(error)) from error
        if not isinstance(data, dict): raise ValueError('配置必须是 JSON 对象：' + str(self.path))
        value = copy.deepcopy(DEFAULTS[self.kind])
        value.update(data)
        if self.kind == 'management': value.pop('slaves', None)
        self.validate(value)
        return value

    def validate(self, value):
        if self.kind == 'host':
            for key in ('slaves', 'current_slaves', 'relay_grants'):
                if not isinstance(value[key], list) or any(not isinstance(x, str) for x in value[key]):
                    raise ValueError(key + ' 必须是字符串数组')
            if not isinstance(value['llm'], dict): raise ValueError('llm 必须是对象')
            if not isinstance(value['headless'], bool): raise ValueError('headless 必须是布尔值')
            if not isinstance(value['management_bind'], str): raise ValueError('management_bind 必须是字符串')
            if not isinstance(value['management_interval'], (float, int)) or value['management_interval'] <= 0:
                raise ValueError('management_interval 必须大于0')
            port = value['management_port']
        elif self.kind == 'slave':
            if not isinstance(value['root'], str) or not value['root'].strip(): raise ValueError('root 不能为空')
            if not isinstance(value['name'], str) or not value['name'].strip(): raise ValueError('name 不能为空')
            port = value['port']
        else:
            if value['role'] not in ('host', 'slave'): raise ValueError('role 必须为 host 或 slave')
            try: ipaddress.IPv4Address(value['host_ip'])
            except (ValueError, TypeError): raise ValueError('host_ip 必须是有效的 IPv4 地址')
            return
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError('监听端口必须为1至65535')
        if value['log_level'] not in ('DEBUG', 'INFO', 'WARNING', 'ERROR'): raise ValueError('无效 log_level')

    def update(self, changes):
        value = self.load()
        value.update(changes)
        self.validate(value)
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as output:
                if os.name != 'nt': temporary.chmod(0o600)
                json.dump(value, output, ensure_ascii=False, indent=2)
                output.write('\n')
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return value
