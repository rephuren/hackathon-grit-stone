"""Web Push для PWA и браузера: уведомления о заказе и смене статуса.

Бесплатно и без сторонних сервисов, кроме push-сервиса самого браузера (Apple, Google, Mozilla): по стандарту
Web Push сообщение шифруется ключом подписки, push-сервис видит только зашифрованный текст.
Ключи VAPID создаются при первом запуске и хранятся в data/vapid.json (не в репозитории).
Ограничения: на iPhone push работает только у приложения, добавленного на экран «Домой» (iOS 16.4+);
в Telegram Mini App web push недоступен — там уведомления шлёт бот (tgbot.py).
Ошибки отправки не ломают заказ: просроченные подписки (404/410) удаляются, остальное пишется в журнал."""
import base64
import json
import logging
import os
import threading

from . import db as D

log = logging.getLogger("picpac.push")

try:  # необязательная зависимость: без неё приложение работает, push просто выключен
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from pywebpush import WebPushException, webpush
    AVAILABLE = True
except Exception:  # pragma: no cover
    AVAILABLE = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS push_subs(
  endpoint TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  p256dh TEXT NOT NULL, auth TEXT NOT NULL, ua TEXT, created_at TEXT NOT NULL, last_ok TEXT, last_error TEXT);
CREATE INDEX IF NOT EXISTS idx_push_user ON push_subs(user_id);
"""

_keys = None
_lock = threading.Lock()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def init(conn) -> None:
    conn.executescript(SCHEMA)


def keys() -> dict | None:
    """{'public': base64url для браузера, 'private_pem': PEM для подписи}. Создаются один раз."""
    global _keys
    if not AVAILABLE:
        return None
    with _lock:
        if _keys:
            return _keys
        path = os.path.join(D.data_dir(), "vapid.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                _keys = json.load(fh)
            return _keys
        pk = ec.generate_private_key(ec.SECP256R1())
        pub = pk.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        pem = pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption()).decode()
        _keys = {"public": _b64(pub), "private_pem": pem}
        os.makedirs(D.data_dir(), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(_keys, fh)
        os.chmod(path, 0o600)
        return _keys


def enabled() -> bool:
    return AVAILABLE and os.environ.get("WEBPUSH", "1") == "1"


def subject() -> str:
    # Контакт для push-сервисов по стандарту VAPID. Без личных данных: адрес стенда или нейтральный mailto.
    base = (os.environ.get("WEBAPP_URL") or "").strip()
    return base if base.startswith("https://") else "mailto:noreply@picpac.test"


def save(conn, user_id: int, sub: dict, ua: str = "") -> None:
    conn.execute("INSERT INTO push_subs(endpoint,user_id,p256dh,auth,ua,created_at) VALUES(?,?,?,?,?,?) "
                 "ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id,p256dh=excluded.p256dh,"
                 "auth=excluded.auth,ua=excluded.ua",
                 (sub["endpoint"], user_id, sub["keys"]["p256dh"], sub["keys"]["auth"], ua[:200], D.now_iso()))


def remove(conn, user_id: int, endpoint: str | None = None) -> int:
    if endpoint:
        return conn.execute("DELETE FROM push_subs WHERE user_id=? AND endpoint=?", (user_id, endpoint)).rowcount
    return conn.execute("DELETE FROM push_subs WHERE user_id=?", (user_id,)).rowcount


def count(conn, user_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM push_subs WHERE user_id=?", (user_id,)).fetchone()[0]


def _send_one(row, payload: dict) -> tuple[bool, str]:
    k = keys()
    try:
        webpush(subscription_info={"endpoint": row["endpoint"], "keys": {"p256dh": row["p256dh"], "auth": row["auth"]}},
                data=json.dumps(payload, ensure_ascii=False), vapid_private_key=_vapid_obj(k),
                vapid_claims={"sub": subject()}, ttl=86400, timeout=10)
        return True, "ok"
    except WebPushException as exc:
        code = getattr(exc.response, "status_code", 0) if exc.response is not None else 0
        return False, f"http_{code}" if code else "push_error"
    except Exception as exc:
        return False, type(exc).__name__


def _vapid_obj(k):
    from py_vapid import Vapid
    return Vapid.from_pem(k["private_pem"].encode())


def send_to_user(user_id: int, title: str, body: str, url: str = "/") -> dict:
    """Синхронно отправляет на все подписки пользователя. Возвращает {'sent': n, 'failed': m, 'errors': [...]}."""
    if not enabled():
        return {"sent": 0, "failed": 0, "errors": ["disabled"]}
    with D.db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM push_subs WHERE user_id=?", (user_id,))]
    sent, errors = 0, []
    payload = {"title": title, "body": body, "url": url, "tag": url}
    for r in rows:
        ok, why = _send_one(r, payload)
        with D.db() as c:
            if ok:
                sent += 1
                c.execute("UPDATE push_subs SET last_ok=?, last_error=NULL WHERE endpoint=?", (D.now_iso(), r["endpoint"]))
            elif why in ("http_404", "http_410"):  # подписка отозвана браузером — убираем
                c.execute("DELETE FROM push_subs WHERE endpoint=?", (r["endpoint"],))
                errors.append(why)
            else:
                c.execute("UPDATE push_subs SET last_error=? WHERE endpoint=?", (why, r["endpoint"]))
                errors.append(why)
        if not ok:
            log.warning("web push user=%s: %s", user_id, why)
    with D.db() as c:
        D.audit(c, {"id": user_id, "role": "client"}, "push_send", f"user:{user_id}",
                "ok" if sent and not errors else ("fail" if errors else "none"), f"sent={sent} failed={len(errors)}")
    return {"sent": sent, "failed": len(errors), "errors": errors}


def notify_user(user_id: int, title: str, body: str, url: str = "/") -> None:
    """Фоновая отправка: заказ и смена статуса не ждут push-сервис."""
    if enabled():
        threading.Thread(target=send_to_user, args=(user_id, title, body, url), daemon=True).start()
