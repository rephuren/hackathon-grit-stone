"""Telegram-бот picPac: кнопка запуска Mini App, команды /start и /help, уведомления о статусе заказа.

Работает через Bot API по HTTPS (httpx), без вебхуков: бот сам опрашивает Telegram (long polling), поэтому
серверу не нужен входящий адрес для бота. Но САМО Mini App должно открываться по публичному HTTPS-адресу
(WEBAPP_URL), например через бесплатный туннель Cloudflare. Токен бота хранится только в .env."""
import asyncio
import logging
import os
import threading

import httpx

from . import db as D

log = logging.getLogger("picpac.tg")
API = "https://api.telegram.org/bot{token}/{method}"


def token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def webapp_url() -> str:
    return os.environ.get("WEBAPP_URL", "").strip().rstrip("/")


def enabled() -> bool:
    return bool(token())


def call(method: str, payload: dict | None = None, timeout: float = 15.0) -> dict:
    """Синхронный вызов Bot API. Ошибки не поднимаем наружу: уведомления не должны ломать заказ."""
    try:
        r = httpx.post(API.format(token=token(), method=method), json=payload or {}, timeout=timeout)
        data = r.json()
    except Exception as exc:  # сеть, не-JSON
        log.warning("telegram %s failed: %s", method, type(exc).__name__)
        return {"ok": False, "description": type(exc).__name__}
    if not data.get("ok"):
        log.warning("telegram %s: %s", method, data.get("description"))
    return data


def open_button(text: str = "Открыть picPac") -> dict | None:
    url = webapp_url()
    if not url.startswith("https://"):
        return None  # Telegram принимает Mini App только по HTTPS
    return {"inline_keyboard": [[{"text": text, "web_app": {"url": url}}]]}


def send(chat_id: str, text: str, with_button: bool = False):
    payload = {"chat_id": chat_id, "text": text}
    if with_button and open_button():
        payload["reply_markup"] = open_button()
    return call("sendMessage", payload)


def notify_user(user_id: int, text: str):
    """Сообщение пользователю Telegram по его id в нашей базе. Для гостей и входа по паролю ничего не делает."""
    if not enabled():
        return
    with D.db() as c:
        row = c.execute("SELECT tg_id FROM users WHERE id=?", (user_id,)).fetchone()
    if row and row["tg_id"]:
        threading.Thread(target=send, args=(row["tg_id"], text, True), daemon=True).start()


def setup() -> dict:
    """Один раз настраивает бота: имя в списке команд, описание и кнопку меню, открывающую Mini App."""
    me = call("getMe")
    if not me.get("ok"):
        return {"ok": False, "error": me.get("description", "токен не принят")}
    call("setMyCommands", {"commands": [{"command": "start", "description": "Открыть picPac"},
                                        {"command": "help", "description": "Как это работает"}]})
    call("setMyDescription", {"description": "Соберите фотокнигу из фото на телефоне: отбор, правки, проверка и заказ."})
    call("setMyShortDescription", {"short_description": "Фотокниги и печать фото picPac"})
    menu = False
    if open_button():
        menu = call("setChatMenuButton", {"menu_button": {"type": "web_app", "text": "picPac", "web_app": {"url": webapp_url()}}}).get("ok", False)
    return {"ok": True, "username": me["result"].get("username"), "menu_button": menu, "webapp_url": webapp_url() or None}


WELCOME = ("Привет! Это picPac: фотокнига из ваших фото за несколько шагов.\n"
           "Выберите снимки, поправьте, проверим перед печатью и оформим тестовый заказ.")


async def poll_loop():
    """Отвечает на /start и /help. Если адрес Mini App не задан, просто объясняет, что запуск ещё не настроен."""
    offset = 0
    await asyncio.to_thread(call, "deleteWebhook")
    while True:
        try:
            res = await asyncio.to_thread(call, "getUpdates", {"offset": offset, "timeout": 25, "allowed_updates": ["message"]}, 40.0)
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                text = (msg.get("text") or "").strip()
                chat = (msg.get("chat") or {}).get("id")
                if chat and msg.get("chat", {}).get("type") == "private" and text.startswith(("/start", "/help")):
                    note = WELCOME if open_button() else WELCOME + "\n\nЗапуск приложения ещё настраивается."
                    await asyncio.to_thread(send, str(chat), note, True)
            if not res.get("ok"):
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("poll loop")
            await asyncio.sleep(5)
