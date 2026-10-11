"""Collect installed dependency notices alongside Windows executables."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    packages = ('PySide6-Essentials', 'shiboken6', 'numpy', 'scipy', 'Pillow',
                'sounddevice', 'soundfile', 'cffi', 'pycparser', 'requests',
                'urllib3', 'certifi', 'charset-normalizer', 'idna', 'pyinstaller')
    versions = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for package in packages:
            try:
                distribution = importlib.metadata.distribution(package)
            except importlib.metadata.PackageNotFoundError:
                continue
            versions[package] = distribution.version
            for member in distribution.files or []:
                if not any(word in str(member).lower() for word in ('license', 'copying', 'copyright', 'notice')):
                    continue
                path = distribution.locate_file(member)
                if path.is_file(): archive.write(path, package + '/' + str(member).replace('..', '_'))
        for name in ('LICENSE.txt', 'LICENSE_PYTHON.txt'):
            path = Path(sys.base_prefix) / name
            if path.is_file(): archive.write(path, 'python/' + name)
        archive.writestr('DEPENDENCY_VERSIONS.json', json.dumps(versions, indent=2))
    print(args.output, args.output.stat().st_size)


if __name__ == '__main__':
    main()
