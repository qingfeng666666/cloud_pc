"""Prepare portable Linux source runtime and the Windows build source archive."""
import argparse
from pathlib import Path
import shutil
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    runtime = output / 'runtime'
    target = runtime / 'cloud_pc'
    directories = ('host_program', 'desktop_version/host', 'shared_components/common',
                   'shared_components/slave', 'management_ui')
    for directory in directories:
        for path in (source / directory).rglob('*.py'):
            if 'tests' in path.parts or path.name == 'package_llm_release.py':
                continue
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    extra = ['shared_components/cloud_slave_entry.py', 'shared_components/windows_version_info.txt',
             'host_program/llm_config.json', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md',
             'host.example.json', 'slave.example.json', 'management.example.json']
    extra += [path.name for path in source.glob('requirements*.txt')]
    for name in extra:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, destination)
    shutil.copytree(source / 'docs', target / 'docs', dirs_exist_ok=True)
    shutil.copytree(source / 'android_slave/app/src/main/assets/licenses', target / 'third_party_licenses',
                    dirs_exist_ok=True)
    for label, module in [('主机', 'host_program.main'), ('从机', 'shared_components.slave.main'),
                          ('管理界面', 'management_ui.main')]:
        script = output / ('启动' + label + '.sh')
        script.write_text('#!/usr/bin/env bash\nset -e\n'
                          'export CLOUD_CONFIG_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"\n'
                          'cd -- "$CLOUD_CONFIG_DIR/runtime"\n'
                          'exec python3 -m cloud_pc.' + module + ' "$@"\n', encoding='utf-8')
        script.chmod(0o755)
    archive = output / 'cloud-pc-linux-1.0.0.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in runtime.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.pyo'):
                bundle.write(path, path.relative_to(output))
        for path in output.glob('*.sh'): bundle.write(path, path.name)
    print(archive, archive.stat().st_size)


if __name__ == '__main__':
    main()
