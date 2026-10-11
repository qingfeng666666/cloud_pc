"""Build the three Windows applications in an isolated release directory."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--pip-index-url', default='https://pypi.org/simple')
    parser.add_argument('--targets', nargs='+', choices=('CloudHost', 'CloudSlave', 'CloudManagement'),
                        default=['CloudHost', 'CloudSlave', 'CloudManagement'])
    args = parser.parse_args()
    root = args.root.resolve()
    runtime = root / 'runtime'
    source = runtime / 'cloud_pc'
    dist = root / 'dist'
    dist.mkdir(exist_ok=True)
    for module, package in [('PyInstaller', 'pyinstaller'), ('PySide6', 'PySide6-Essentials'),
                            ('PIL', 'Pillow'), ('numpy', 'numpy'), ('scipy', 'scipy'),
                            ('sounddevice', 'sounddevice')]:
        if importlib.util.find_spec(module) is None:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--index-url', args.pip_index_url, package])
    common = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
              '--paths', str(runtime), '--paths', str(source / 'shared_components'),
              '--paths', str(source / 'desktop_version'), '--distpath', str(dist),
              '--version-file', str(source / 'shared_components/windows_version_info.txt'),
              '--add-data', str(source / 'LICENSE') + ';.',
              '--add-data', str(source / 'THIRD_PARTY_NOTICES.md') + ';.']
    entries = {'CloudHost': source / 'host_program/windows_entry.py',
               'CloudSlave': source / 'shared_components/cloud_slave_entry.py',
               'CloudManagement': source / 'management_ui/windows_entry.py'}
    for name, entry in entries.items():
        if name not in args.targets:
            continue
        extra = []
        if name == 'CloudHost':
            extra += ['--hidden-import', 'host.main', '--add-data',
                      str(source / 'host_program/llm_config.json') + ';cloud_pc/host_program']
        if name == 'CloudSlave':
            extra += ['--collect-submodules', 'slave']
        if name == 'CloudManagement':
            extra += ['--hidden-import', 'PIL.ImageTk']
        # Keep a console for --version and diagnostics. Management remains a separate UI.
        subprocess.check_call(common + ['--name', name, '--workpath', str(root / 'build' / name),
                              '--specpath', str(root / 'build')] + extra + [str(entry)])
        result = subprocess.run([str(dist / (name + '.exe')), '--version'],
                                cwd=dist, capture_output=True, text=True, timeout=60)
        if result.returncode or result.stdout.strip() != '1.0.0':
            raise RuntimeError(f'{name} version smoke failed: {result.stdout} {result.stderr}')
        print('VERSION_SMOKE_OK', name, flush=True)
    env = os.environ.copy()
    result = subprocess.run([str(dist / 'CloudManagement.exe'), '--smoke-test'], cwd=dist,
                            env=env, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('Management UI smoke failed: ' + result.stderr)
    print('MANAGEMENT_UI_SMOKE_OK', result.stdout.strip(), flush=True)
    subprocess.check_call([sys.executable, str(source / 'host_program/collect_binary_licenses.py'),
                           '--output', str(dist / 'ThirdPartyLicenses.zip')])
    manifest = {'version': '1.0.0', 'files': {}}
    for name in [name + '.exe' for name in entries] + ['ThirdPartyLicenses.zip']:
        path = dist / name
        manifest['files'][path.name] = {'bytes': path.stat().st_size,
                                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    (root / 'build_result.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('BUILD_COMPLETE', flush=True)


if __name__ == '__main__':
    main()
