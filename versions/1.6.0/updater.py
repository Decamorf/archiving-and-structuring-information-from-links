#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проверка и загрузка обновлений из GitHub Releases.

Логика:
- спрашивает у GitHub последний релиз репозитория;
- сравнивает его тег (vX.Y.Z) с текущей версией по правилам SemVer;
- если новее — сообщает и (по желанию пользователя) скачивает
  установщик Архиватор_setup.exe из ассетов релиза.

Ничего не устанавливает молча: только проверяет, уведомляет и, если
пользователь согласился, скачивает файл и открывает его.
"""

import os
import json
import urllib.request

# ВАЖНО: укажите свой репозиторий (owner/repo)
GITHUB_REPO = "Decamorf/archiving-and-structuring-information-from-links"
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse_version(s: str):
    """'v1.5.0' / '1.5.0' -> (1,5,0). Нечисловые части игнорируются."""
    s = (s or "").lstrip("vV").strip()
    parts = []
    for p in s.split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(remote_tag: str, current: str) -> bool:
    return _parse_version(remote_tag) > _parse_version(current)


def check_latest(current_version: str, timeout: int = 10) -> dict:
    """Возвращает dict:
      {'available': bool, 'version': str, 'url': str (страница релиза),
       'asset_url': str (ссылка на .exe или ''), 'notes': str}
    При ошибке сети — {'available': False, 'error': '...'}.
    """
    try:
        req = urllib.request.Request(
            API_URL, headers={"Accept": "application/vnd.github+json",
                              "User-Agent": "Archiver-Updater"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)}

    tag = data.get("tag_name") or ""
    asset_url = ""
    for a in data.get("assets") or []:
        name = (a.get("name") or "").lower()
        if name.endswith(".exe"):
            asset_url = a.get("browser_download_url") or ""
            break
    return {
        "available": is_newer(tag, current_version),
        "version": tag.lstrip("vV"),
        "url": data.get("html_url") or "",
        "asset_url": asset_url,
        "notes": (data.get("body") or "").strip(),
    }


def download_installer(asset_url: str, dest_dir: str, log) -> str:
    """Скачивает установщик в dest_dir, возвращает путь к файлу."""
    if not asset_url:
        raise RuntimeError("У релиза нет прикреплённого установщика (.exe)")
    fname = asset_url.split("/")[-1] or "Архиватор_setup.exe"
    dest = os.path.join(dest_dir, fname)
    log("Скачиваю обновление...")
    req = urllib.request.Request(
        asset_url, headers={"User-Agent": "Archiver-Updater"})
    with urllib.request.urlopen(req, timeout=60) as r, \
            open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        last_pct = -1
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                pct = got * 100 // total
                if pct >= last_pct + 10:
                    last_pct = pct
                    log(f"...загружено {pct}%")
    log(f"Обновление скачано: {dest}")
    return dest
