# -*- mode: python ; coding: utf-8 -*-

import json
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)


contract = json.loads((Path(SPECPATH) / 'release-contract.json').read_text(encoding='utf-8'))
metadata = contract['windows_metadata']
numeric_version = tuple(metadata['numeric_version'])
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=numeric_version,
        prodvers=numeric_version,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    '040904B0',
                    [
                        StringStruct(key, metadata[key])
                        for key in (
                            'CompanyName',
                            'FileDescription',
                            'FileVersion',
                            'InternalName',
                            'OriginalFilename',
                            'ProductName',
                            'ProductVersion',
                        )
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct('Translation', [1033, 1200])]),
    ],
)


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('web', 'web'),
        ('bootstrap.sh', '.'),
        ('image-manifest.json', '.'),
        ('release-contract.json', '.'),
    ],  # Includes vendor/fonts, vendor/xterm, vendor/fontawesome
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
    name='KACE-studio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='web/KACE-studio.ico',
    version=version_info,
)
