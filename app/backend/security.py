"""Пароли, токены, подписанные ссылки на файлы, проверка Telegram initData."""
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import parse_qsl

# Секрет берётся из окружения. Если не задан, генерируется на время работы процесса.
APP_SECRET = os.environ.get("APP_SECRET") or secrets.token_hex(32)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return salt.hex() + "$" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 120_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, sha(token)


def sign_file(photo_id: str, size: str, exp: int) -> str:
    msg = f"{photo_id}:{size}:{exp}".encode()
    return hmac.new(APP_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:32]


def verify_file_sig(photo_id: str, size: str, exp: int, sig: str) -> bool:
    return exp >= int(time.time()) and hmac.compare_digest(sign_file(photo_id, size, exp), sig or "")


def verify_telegram_init_data(init_data: str, bot_token: str, max_age: int = 86400):
    """Проверка подписи Telegram Mini App (https://core.telegram.org/bots/webapps#validating-data).
    Возвращает dict пользователя или None. Подделать user id без токена бота нельзя."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received = pairs.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not received or not hmac.compare_digest(calc, received):
            return None
        if time.time() - int(pairs.get("auth_date", "0")) > max_age:
            return None
        return json.loads(pairs["user"])
    except Exception:
        return None
