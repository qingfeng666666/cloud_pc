"""Fetch completed Windows build artifacts exclusively through the local host."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import build_opener, ProxyHandler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--slave', required=True)
    parser.add_argument('--remote-root', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    opener = build_opener(ProxyHandler({}))

    def download(path):
        url = 'http://127.0.0.1:19527/api/' + quote(args.slave, safe='') + '/download?' + urlencode({'path': path})
        with opener.open(url, timeout=120) as response:
            result = json.load(response)
        if result.get('error'): raise RuntimeError(result['error'])
        return base64.b64decode(result['base64']) if 'base64' in result else result['text'].encode()

    manifest_data = download(args.remote_root + '/build_result.json')
    manifest = json.loads(manifest_data)
    assert manifest['version'] == '1.0.0'
    args.output.mkdir(parents=True, exist_ok=True)
    for name, expected in manifest['files'].items():
        if Path(name).name != name or '\\' in name: raise ValueError('Unsafe artifact filename')
        data = download(args.remote_root + '/dist/' + name)
        assert len(data) == expected['bytes'] and hashlib.sha256(data).hexdigest() == expected['sha256'], name
        (args.output / name).write_bytes(data)
        print('DOWNLOADED_SHA256_MATCH', name, len(data), flush=True)
    (args.output / 'build_result.json').write_bytes(manifest_data)


if __name__ == '__main__':
    main()
