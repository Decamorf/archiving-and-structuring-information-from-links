# -*- mode: python ; coding: utf-8 -*-
# Сборка Windows: pyinstaller build_windows.spec
block_cipher = None

a = Analysis(
    ['app_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('ИНСТРУКЦИЯ.md', '.'), ('FAQ.md', '.'), ('icon.png', '.')],
    hiddenimports=[
        'faster_whisper', 'yt_dlp', 'instaloader', 'trafilatura',
        'bs4', 'flask', 'werkzeug', 'PIL', 'imageio_ffmpeg',
        'huggingface_hub', 'requests', 'analyzer', 'setup_deps',
        'archiver_web', 'bot', 'updater',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='Архиватор',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # без чёрного окна
    icon='icon.ico',        # если файла нет — просто уберите эту строку
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[],
    name='Архиватор',
)
