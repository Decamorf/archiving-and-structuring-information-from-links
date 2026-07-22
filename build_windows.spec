# -*- mode: python ; coding: utf-8 -*-
# Сборка Windows: pyinstaller build_windows.spec
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
block_cipher = None

# файлы данных из пакетов, которые PyInstaller иначе пропускает
_extra_datas = [('ИНСТРУКЦИЯ.md', '.'), ('FAQ.md', '.'), ('icon.png', '.')]
for pkg in ('faster_whisper', 'ctranslate2', 'tokenizers'):
    try:
        _extra_datas += collect_data_files(pkg)
    except Exception:
        pass
_extra_bins = []
for pkg in ('ctranslate2', 'onnxruntime'):
    try:
        _extra_bins += collect_dynamic_libs(pkg)
    except Exception:
        pass

a = Analysis(
    ['app_launcher.py'],
    pathex=[],
    binaries=_extra_bins,
    datas=_extra_datas,
    hiddenimports=[
        'faster_whisper', 'yt_dlp', 'instaloader', 'trafilatura',
        'bs4', 'flask', 'werkzeug', 'PIL', 'imageio_ffmpeg',
        'huggingface_hub', 'requests', 'analyzer', 'setup_deps',
        'archiver_web', 'bot', 'updater', 'onnxruntime', 'ctranslate2',
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
