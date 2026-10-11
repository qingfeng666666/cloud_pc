# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['host\\main.py'],
    pathex=['../..', '../shared_components'],
    binaries=[],
    datas=[('..\\LICENSE', '.'), ('..\\THIRD_PARTY_NOTICES.md', '.'),
           ('..\\shared_components\\common', 'common'), ('host', 'host'),
           ('..\\host_program\\llm_config.json', 'cloud_pc/host_program')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CloudHost',
    version='../shared_components/windows_version_info.txt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
