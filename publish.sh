#!/bin/bash
cd "$(dirname "$0")"
VER=$(grep -oP 'APP_VERSION = "\K[^"]+' archiver.py)
echo "Publishing version $VER to GitHub..."
git add -A
git commit -m "Version $VER"
git tag "v$VER" 2>/dev/null
git push origin main --tags
echo "Done."
