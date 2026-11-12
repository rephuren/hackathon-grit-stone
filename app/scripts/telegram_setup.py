"""Настройка бота: python scripts/telegram_setup.py   (нужны TELEGRAM_BOT_TOKEN и WEBAPP_URL в app/.env)
Проверяет токен, ставит команды, описание и кнопку меню, которая открывает Mini App."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from backend import tgbot  # noqa: E402

if not tgbot.enabled():
    sys.exit("В app/.env нет TELEGRAM_BOT_TOKEN (токен выдаёт @BotFather).")
res = tgbot.setup()
print(json.dumps(res, ensure_ascii=False, indent=2))
if res.get("ok") and not res.get("menu_button"):
    print("\nКнопка меню не поставлена: задайте в app/.env WEBAPP_URL=https://... (адрес должен быть HTTPS) и запустите снова.")
