"""SQLite: схема, подключение, начальные тестовые данные."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))


def data_dir() -> str:
    return os.environ.get("DATA_DIR") or os.path.join(BASE, "..", "data")


def db_path() -> str:
    return os.environ.get("DB_PATH") or os.path.join(data_dir(), "app.db")


def upload_dir() -> str:
    return os.path.join(data_dir(), "uploads")


def ai_tmp_dir() -> str:
    return os.path.join(data_dir(), "ai_tmp")


def now_iso() -> str:
    """Время всегда в UTC с часовым поясом (ОТ-07)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db():
    os.makedirs(os.path.dirname(os.path.abspath(db_path())), exist_ok=True)
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # WAL: читатели не блокируют писателя (жюри, бот и команда работают одновременно); режим хранится в файле БД
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  login TEXT UNIQUE, pass_hash TEXT, role TEXT NOT NULL DEFAULT 'client',
  name TEXT, tg_id TEXT UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  product_id TEXT NOT NULL, title TEXT NOT NULL, settings TEXT NOT NULL,
  layout TEXT, status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS photos(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL, orig_name TEXT, width INTEGER, height INTEGER, taken_at TEXT,
  blur REAL, brightness REAL, ahash TEXT, position INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, edits TEXT, qx TEXT);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  project_id TEXT, project_title TEXT, product_title TEXT, photos_count INTEGER,
  idem_key TEXT NOT NULL, status TEXT NOT NULL, total_kop INTEGER NOT NULL,
  price TEXT NOT NULL, delivery TEXT NOT NULL, promo TEXT, eta TEXT, created_at TEXT NOT NULL,
  UNIQUE(user_id, idem_key));
CREATE TABLE IF NOT EXISTS order_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  status TEXT NOT NULL, ts TEXT NOT NULL, actor TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS tickets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, user_id INTEGER, role TEXT,
  action TEXT NOT NULL, object TEXT, result TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, user_id INTEGER, name TEXT NOT NULL, props TEXT);
CREATE INDEX IF NOT EXISTS idx_photos_project ON photos(project_id);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
"""


def audit(conn, user, action: str, obj: str = "", result: str = "ok", detail: str = ""):
    """Журнал действий: кто, когда, что, над чем и с каким результатом (ОТ-04)."""
    conn.execute(
        "INSERT INTO audit(ts,user_id,role,action,object,result,detail) VALUES(?,?,?,?,?,?,?)",
        (now_iso(), user["id"] if user else None, user["role"] if user else None,
         action, obj, result, detail[:500]),
    )


def init_db():
    from . import catalog, security

    with db() as c:
        c.executescript(SCHEMA)
        # Каталог — версионируемые mock-данные приложения. Upsert добавляет новые продукты
        # (например, календарь) и обновляет цены на существующем стенде без очистки проектов.
        for p in catalog.PRODUCTS:
            c.execute(
                "INSERT INTO products(id,kind,title,data) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,title=excluded.title,data=excluded.data",
                (p["id"], p["kind"], p["title"], json.dumps(p, ensure_ascii=False)),
            )
        if os.environ.get("SEED_TEST_USERS", "1") == "1":
            for login, pw, role, name in [
                ("demo", "demo123", "client", "Анна (тест)"),
                ("demo2", "demo123", "client", "Игорь (тест)"),
                ("operator", "operator123", "operator", "Оператор (тест)"),
            ]:
                if not c.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
                    c.execute("INSERT INTO users(login,pass_hash,role,name,created_at) VALUES(?,?,?,?,?)",
                              (login, security.hash_password(pw), role, name, now_iso()))
        op_pw = os.environ.get("OPERATOR_PASSWORD", "")
        if op_pw:  # для публичного стенда: свой пароль оператора вместо тестового
            h = security.hash_password(op_pw)
            if c.execute("SELECT 1 FROM users WHERE login='operator'").fetchone():
                c.execute("UPDATE users SET pass_hash=? WHERE login='operator'", (h,))
            else:
                c.execute("INSERT INTO users(login,pass_hash,role,name,created_at) VALUES('operator',?,'operator','Оператор',?)", (h, now_iso()))
        cols = [r["name"] for r in c.execute("PRAGMA table_info(photos)")]
        if "edits" not in cols:  # миграция для баз, созданных до появления редактора
            c.execute("ALTER TABLE photos ADD COLUMN edits TEXT")
        if "qx" not in cols:  # расширенные метрики качества (JSON)
            c.execute("ALTER TABLE photos ADD COLUMN qx TEXT")
        if "pinned" not in cols:  # Smart Story: закреплённый кадр получает свою страницу
            c.execute("ALTER TABLE photos ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        if "excluded" not in cols:  # Smart Story: фото убрано из книги, но не удалено
            c.execute("ALTER TABLE photos ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0")
    os.makedirs(upload_dir(), exist_ok=True)
    os.makedirs(ai_tmp_dir(), exist_ok=True)
