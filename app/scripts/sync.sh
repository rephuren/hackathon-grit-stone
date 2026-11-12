#!/bin/bash
# Подтягивает правки команды из GitHub для стенда. Фронтенд подхватывается сразу (отдаётся с диска),
# для бэкенда нужен перезапуск стенда: скрипт скажет, нужен ли он.  Запуск: bash app/scripts/sync.sh
set -e
cd "$(dirname "$0")/../.."
if [ -n "$(git status --porcelain)" ]; then echo "В папке есть несохранённые изменения. Сначала закоммитьте их (git status)."; exit 1; fi
before=$(git rev-parse HEAD)
git pull --ff-only origin main
after=$(git rev-parse HEAD)
if [ "$before" = "$after" ]; then echo "Новых правок нет. Версия: $(cat app/VERSION)"; exit 0; fi
echo "Подтянуто:"; git log --oneline "$before..$after"
if git diff --name-only "$before" "$after" | grep -qE '^app/(backend/|requirements|VERSION)'; then
  echo ">>> Изменён бэкенд: стенд нужно перезапустить."
else
  echo ">>> Изменён только фронтенд/документы: на телефонах появится после перезагрузки приложения."
fi
