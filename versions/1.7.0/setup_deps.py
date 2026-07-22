#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проверка и автоматическое доустановление тяжёлых компонентов при первом
запуске: модель Whisper (обязательно), ffmpeg (для сжатия видео),
Ollama (по желанию — умные конспекты).

Используется и оконной версией (archiver.py), и серверной
(archiver_web.py). Все функции принимают log(msg) для вывода прогресса.
"""

import os
import shutil


def whisper_model_present(model_size: str = "small") -> bool:
    """True, если модель Whisper уже скачана (офлайн-готова)."""
    try:
        from huggingface_hub import scan_cache_dir
        wanted = f"faster-whisper-{model_size}"
        for repo in scan_cache_dir().repos:
            if wanted in repo.repo_id.lower():
                return True
    except Exception:  # noqa: BLE001
        pass
    # запасная эвристика: ищем в стандартном кэше HF
    home = os.path.expanduser("~")
    for base in (os.path.join(home, ".cache", "huggingface"),
                 os.environ.get("HF_HOME", "")):
        if base and os.path.isdir(base):
            for root, _, files in os.walk(base):
                if f"faster-whisper-{model_size}" in root.lower() \
                        and any(f.endswith(".bin") for f in files):
                    return True
    return False


def ensure_whisper(model_size: str, log) -> bool:
    """Гарантирует наличие модели Whisper. Качает при отсутствии.
    Возвращает True, если модель готова к работе."""
    if whisper_model_present(model_size):
        return True
    log(f"Первый запуск: скачиваю модель распознавания речи "
        f"«{model_size}» (один раз, нужен интернет)...")
    try:
        from faster_whisper import WhisperModel
        # само создание модели скачивает веса в кэш
        WhisperModel(model_size, device="cpu", compute_type="int8")
        log("Модель распознавания речи готова.")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"Не удалось скачать модель Whisper: {e}. "
            f"Проверьте интернет и перезапустите.")
        return False


def ffmpeg_path() -> str:
    """Путь к ffmpeg (системный или встроенный imageio) или ''."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return ""


def ffmpeg_present() -> bool:
    return bool(ffmpeg_path())


def ollama_status():
    """('ok', model) / ('no_model', '') / ('offline', '') — см. analyzer."""
    try:
        import analyzer
        return analyzer.status()
    except Exception:  # noqa: BLE001
        return ("offline", "")


def components_report() -> dict:
    """Сводка готовности для показа в окне."""
    st, model = ollama_status()
    return {
        "whisper": whisper_model_present("small"),
        "ffmpeg": ffmpeg_present(),
        "ollama": st,          # ok / no_model / offline
        "ollama_model": model,
    }


def first_run_setup(model_size: str, log) -> None:
    """Полная проверка при старте: докачивает Whisper, сообщает о
    состоянии ffmpeg и Ollama. Ничего не требует от пользователя."""
    ensure_whisper(model_size, log)

    if not ffmpeg_present():
        log("ffmpeg не найден — сжатие видео будет недоступно "
            "(обычно ставится автоматически из requirements).")

    st, model = ollama_status()
    if st == "ok":
        log(f"Умные конспекты включены (локальный ИИ: {model}).")
    elif st == "no_model":
        log("Ollama установлен, но модель не скачана. Для умных "
            "конспектов выполните: ollama pull qwen2.5:7b")
    else:
        log("Ollama не найден — работает базовый режим. Умные конспекты "
            "и темы включатся после установки Ollama (см. ИНСТРУКЦИЯ).")
