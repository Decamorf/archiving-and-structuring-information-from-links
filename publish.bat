@echo off
cd /d "%~dp0"
for /f tokens^=2^ delims^=^" %%v in ('findstr /c:"APP_VERSION = " archiver.py') do set VER=%%v
echo Publishing version %VER% to GitHub...
git add -A
git commit -m "Version %VER%"
git tag v%VER% 2>nul
git push origin main --tags
echo.
echo Done. Press any key to close.
pause >nul
