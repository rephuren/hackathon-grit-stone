#!/bin/sh
# Копирует веб-приложение в www и прописывает адрес сервера. Запуск: SERVER=https://ваш-домен sh sync.sh
set -e
cd "$(dirname "$0")"
rm -rf www && mkdir www
cp -R ../app/frontend/. www/
: "${SERVER:?Укажите адрес сервера: SERVER=https://ваш-домен sh sync.sh}"
printf "window.PICPAC_API_BASE = '%s';\n" "$SERVER" > www/config.js
rm -f www/sw.js  # в нативной оболочке service worker не нужен
echo "Готово: www/ (сервер $SERVER)"
