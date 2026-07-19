#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единая точка входа собранного приложения (без консоли).
Открывает окно архиватора; при первом запуске окно само докачает
модель распознавания речи и покажет статус компонентов.

Этот файл — то, что упаковывается в .exe / AppImage.
"""
import sys
import os

# чтобы PyInstaller видел все модули рядом
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    import archiver
    archiver.main()
