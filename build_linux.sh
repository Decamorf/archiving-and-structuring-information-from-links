#!/bin/bash
# Сборка Linux AppImage (двойной клик, работает на любом дистрибутиве).
# Требуется: Python 3.11+, интернет. Запуск: bash build_linux.sh
set -e
cd "$(dirname "$0")"

echo "[1/4] Виртуальное окружение и зависимости..."
python3 -m venv .buildenv
source .buildenv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

echo "[2/4] Сборка исполняемого файла..."
pyinstaller --noconfirm --windowed --name "Архиватор" \
  --add-data "ИНСТРУКЦИЯ.md:." --add-data "FAQ.md:." \
  --hidden-import faster_whisper --hidden-import yt_dlp \
  --hidden-import instaloader --hidden-import trafilatura \
  --hidden-import flask --hidden-import PIL \
  --hidden-import huggingface_hub --hidden-import analyzer \
  --hidden-import setup_deps --hidden-import archiver_web \
  app_launcher.py

echo "[3/4] Упаковка в AppImage..."
APPDIR=Архиватор.AppDir
rm -rf "$APPDIR"; mkdir -p "$APPDIR/usr/bin"
cp -r dist/Архиватор/* "$APPDIR/usr/bin/"
cat > "$APPDIR/AppRun" << 'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/Архиватор" "$@"
EOF
chmod +x "$APPDIR/AppRun"
cat > "$APPDIR/Архиватор.desktop" << 'EOF'
[Desktop Entry]
Name=Архиватор ссылок
Exec=Архиватор
Icon=archiver
Type=Application
Categories=Utility;
EOF
# иконка-заглушка, если своей нет
touch "$APPDIR/archiver.png"

if [ ! -f appimagetool ]; then
  echo "Скачиваю appimagetool..."
  wget -q "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" -O appimagetool
  chmod +x appimagetool
fi
./appimagetool "$APPDIR" "Архиватор-1.2.0-x86_64.AppImage"

echo "[4/4] Готово: Архиватор-1.2.0-x86_64.AppImage"
echo "Запуск у пользователя: chmod +x файл, затем двойной клик."
