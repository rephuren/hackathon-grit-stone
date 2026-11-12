"""Чистый общий демо-аккаунт для команды: python scripts/reset_demo.py
Логин `demo`. Пароль берётся из DEMO_PASSWORD в app/.env; если его нет, создаётся случайный и дописывается туда
(в консоль не печатается). Все проекты, фото, заказы и сессии аккаунта удаляются, поэтому скрипт можно запускать
перед каждым показом, чтобы начать с нуля. Остальные пользователи не затрагиваются."""
import glob
import os
import secrets
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from backend import db as D, security  # noqa: E402

ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
pw = os.environ.get("DEMO_PASSWORD", "")
if not pw:
    pw = "demo-" + secrets.token_hex(4)
    with open(ENV, "a", encoding="utf-8") as f:
        f.write(f"\nDEMO_PASSWORD={pw}\n")
    print("Создан новый пароль демо-аккаунта: см. строку DEMO_PASSWORD в app/.env")

with D.db() as c:
    c.execute("PRAGMA foreign_keys=ON")
    u = c.execute("SELECT id FROM users WHERE login='demo'").fetchone()
    h = security.hash_password(pw)
    if u:
        uid = u["id"]
        c.execute("UPDATE users SET pass_hash=?, name='Демо', role='client' WHERE id=?", (h, uid))
    else:
        uid = c.execute("INSERT INTO users(login,pass_hash,role,name,created_at) VALUES('demo',?,'client','Демо',?)",
                        (h, D.now_iso())).lastrowid
    ids = [r["id"] for r in c.execute("SELECT id FROM photos WHERE user_id=?", (uid,))]
    c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    c.execute("DELETE FROM projects WHERE user_id=?", (uid,))  # фото удаляются каскадом
    c.execute("DELETE FROM orders WHERE user_id=?", (uid,))
    removed = 0
    for pid in ids:
        for f in glob.glob(os.path.join(D.data_dir(), "uploads", "**", pid + "*"), recursive=True):
            os.remove(f)
            removed += 1
print(f"Демо-аккаунт `demo` очищен: фото {len(ids)}, файлов {removed}.")
