#!/bin/sh
# Собирает папку hf-space/ для бесплатного хостинга Hugging Face Spaces (Docker). Запуск: sh deploy/make_space.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/deploy/hf-space"
rm -rf "$OUT"
mkdir -p "$OUT/app"
cp -R "$ROOT/app/backend" "$ROOT/app/frontend" "$ROOT/app/scripts" "$OUT/app/"
cp "$ROOT/app/requirements.txt" "$OUT/app/"
find "$OUT" -name "__pycache__" -type d -prune -exec rm -rf {} +
cp "$ROOT/deploy/Dockerfile" "$ROOT/deploy/README_SPACE.md" "$OUT/"
mv "$OUT/README_SPACE.md" "$OUT/README.md"
echo "Готово: $OUT"
