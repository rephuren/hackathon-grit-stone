"""picPac Mini App — backend (FastAPI + SQLite). Запуск: см. README."""
import asyncio
import io
import json
import math
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from PIL import Image
from pydantic import BaseModel, Field

from . import assistant as AS
from . import catalog as C
from . import editcmd as EC
from . import db as D
from . import imagegen as G
from . import imaging as I
from . import moderation as M
from . import preflight as P
from . import pulse as PULSE
from . import security as S
from . import tgbot as TG
from . import webpush as WP

MAX_FILE = 15 * 1024 * 1024
MAX_PHOTOS = 200
SESSION_DAYS = 14
DEMO_MODE = os.environ.get("DEMO_MODE", "1") == "1"

STATUS_LABELS = {"accepted": "Принят", "moderation": "Модерация", "production": "В производстве",
                 "ready": "Готов / передан в доставку", "completed": "Завершён", "rejected": "Отклонён на модерации"}
TRANSITIONS = {"accepted": ["moderation"], "moderation": ["production", "rejected"],
               "production": ["ready"], "ready": ["completed"]}
FUNNEL = [("app_open", "Открыли приложение"), ("product_view", "Выбрали продукт"),
          ("project_created", "Создали проект"), ("photos_uploaded", "Загрузили фото"),
          ("check_passed", "Прошли проверку"), ("delivery_selected", "Выбрали доставку"),
          ("order_created", "Оформили заказ")]


@asynccontextmanager
async def lifespan(_app):
    D.init_db()
    PULSE.init()  # управленческий контур «Пульс PicPac» (трек 1): свои таблицы pulse_* и тестовые роли
    with D.db() as c:
        WP.init(c)
    if M.available():  # модель модерации грузим заранее: первая загрузка фото не ждёт
        asyncio.create_task(asyncio.to_thread(M.warmup))
    if G.available():  # модель генерации грузим заранее в фоне: первый запрос не ждёт загрузки весов
        asyncio.create_task(asyncio.to_thread(G.warmup))
    poller = None
    if TG.enabled() and TG.webapp_url() and os.environ.get("TELEGRAM_POLLING", "1") == "1":  # опрос только у сервера с публичным адресом: двое сразу конфликтуют
        asyncio.create_task(asyncio.to_thread(TG.setup))  # кнопка меню бота сама начинает вести на этот сервер
        poller = asyncio.create_task(TG.poll_loop())
    yield
    if poller:
        poller.cancel()


app = FastAPI(title="picPac Mini App", version="0.1.0", lifespan=lifespan)
api = APIRouter(prefix="/api")


app.add_middleware(GZipMiddleware, minimum_size=800)
_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
if _origins:  # нативные оболочки (Capacitor и др.) ходят на сервер с другого «домена», им нужен CORS; для сайта и Telegram не нужен
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["Idempotency-Key"])
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")  # для нативных клиентов ссылки на файлы должны быть полными


def _setup_log():
    """Журнал запросов приложения (ОТ-04): метод, путь без query, статус, время. Тела запросов, фото и токены не пишем."""
    import logging
    from logging.handlers import RotatingFileHandler
    lg = logging.getLogger("picpac.http")
    if lg.handlers:
        return lg
    lg.setLevel(logging.INFO)
    lg.propagate = False
    try:
        os.makedirs(D.data_dir(), exist_ok=True)
        h = RotatingFileHandler(os.path.join(D.data_dir(), "app.log"), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)sZ %(message)s", "%Y-%m-%dT%H:%M:%S"))
    logging.Formatter.converter = time.gmtime
    lg.addHandler(h)
    return lg


_HTTPLOG = _setup_log()


@app.middleware("http")
async def security_headers(request, call_next):
    t0 = time.monotonic()
    try:
        resp = await call_next(request)
    except Exception:
        _HTTPLOG.exception("%s %s 500 необработанная ошибка", request.method, request.url.path)
        raise
    if request.url.path.startswith("/api") and request.url.path != "/api/health":
        _HTTPLOG.info("%s %s %s %dms", request.method, request.url.path, resp.status_code, (time.monotonic() - t0) * 1000)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    path = request.url.path
    if not path.startswith("/api"):  # через туннель каждый запрос дорог: статику кэшируем, версия в адресе (?v=)
        if "v" in request.query_params:
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path in ("/", "/index.html"):
            resp.headers["Cache-Control"] = "no-cache"
        elif path.startswith(("/vendor/", "/img/")):
            resp.headers["Cache-Control"] = "public, max-age=86400"
        else:
            resp.headers["Cache-Control"] = "no-cache"
    return resp


def err(status: int, code: str, message: str, **extra):
    return HTTPException(status, detail={"code": code, "message": message, **extra})


# ---------- авторизация ----------
def current_user(authorization: str = Header(default="")) -> dict:
    if not authorization.startswith("Bearer "):
        raise err(401, "unauthorized", "Требуется вход")
    with D.db() as c:
        row = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                        "WHERE s.token_hash=? AND s.expires_at>?",
                        (S.sha(authorization[7:]), D.now_iso())).fetchone()
    if not row:
        raise err(401, "unauthorized", "Сессия истекла, войдите снова")
    return dict(row)


def operator_only(user: dict = Depends(current_user)) -> dict:
    """Права проверяются на сервере (ОТ-02). Отказ фиксируется в журнале."""
    if user["role"] != "operator":
        with D.db() as c:
            D.audit(c, user, "admin_access", "", "denied")
        raise err(403, "forbidden", "Недостаточно прав")
    return user


def start_session(c, user_id: int) -> str:
    token, th = S.new_token()
    exp = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    c.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
              (th, user_id, D.now_iso(), exp))
    return token


def user_public(u) -> dict:
    return {"id": u["id"], "name": u["name"], "role": u["role"], "guest": u["login"] is None and not u["tg_id"],
            "tg": bool(u["tg_id"])}


class LoginIn(BaseModel):
    login: str = Field(max_length=64)
    password: str = Field(max_length=128)
    merge_token: Optional[str] = Field(default=None, max_length=200)  # токен гостя: его проекты перейдут в аккаунт


class TgIn(BaseModel):
    init_data: str = Field(max_length=4096)


# Ограничение частоты попыток входа (защита от перебора пароля). Счётчик в памяти процесса: для прототипа достаточно,
# при нескольких воркерах его нужно вынести в общее хранилище. Ключ включает адрес клиента из X-Forwarded-For
# (за туннелем все запросы приходят с 127.0.0.1), поэтому один злоумышленник не блокирует вход остальным.
_FAILS: dict = {}
_FAIL_LOCK = threading.Lock()
LOGIN_LIMIT, LOGIN_WINDOW = 8, 600      # неудачных попыток на логин с одного адреса за 10 минут
IP_LIMIT = 40                           # неудачных попыток с одного адреса по любым логинам


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "?")


def throttle_check(keys: list):
    now = time.monotonic()
    with _FAIL_LOCK:
        for key, limit in keys:
            hits = [t for t in _FAILS.get(key, []) if now - t < LOGIN_WINDOW]
            _FAILS[key] = hits
            if len(hits) >= limit:
                wait = int(LOGIN_WINDOW - (now - hits[0])) // 60 + 1
                raise err(429, "too_many_attempts", f"Слишком много неудачных попыток входа. Подождите {wait} мин и повторите")


def throttle_fail(keys: list):
    now = time.monotonic()
    with _FAIL_LOCK:
        for key, _ in keys:
            _FAILS.setdefault(key, []).append(now)
        if len(_FAILS) > 5000:  # не даём словарю расти бесконечно
            for k in [k for k, v in _FAILS.items() if not v or now - v[-1] > LOGIN_WINDOW]:
                _FAILS.pop(k, None)


def throttle_clear(key: str):
    with _FAIL_LOCK:
        _FAILS.pop(key, None)


def merge_guest(c, guest_token, target_id: int) -> int:
    """Переносит проекты, фото, заказы и обращения гостя в аккаунт, чтобы при входе ничего не терялось.
    Переносится только настоящий гостевой аккаунт (без логина и Telegram) по его действующему токену."""
    if not guest_token:
        return 0
    g = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?",
                  (S.sha(guest_token), D.now_iso())).fetchone()
    if not g or g["id"] == target_id or g["login"] is not None or g["tg_id"] or g["role"] != "client":
        return 0
    moved = c.execute("SELECT COUNT(*) FROM projects WHERE user_id=?", (g["id"],)).fetchone()[0]
    for table in ("projects", "photos", "orders", "tickets"):
        c.execute(f"UPDATE {table} SET user_id=? WHERE user_id=?", (target_id, g["id"]))
    c.execute("DELETE FROM users WHERE id=?", (g["id"],))  # сессии гостя удаляются каскадом
    d = D.ai_tmp_dir()  # созданные гостем AI-кадры тоже переходят в аккаунт
    if os.path.isdir(d):
        for name in os.listdir(d):
            if name.startswith(f"{g['id']}_"):
                os.replace(os.path.join(d, name), os.path.join(d, f"{target_id}_" + name.split("_", 1)[1]))
    D.audit(c, {"id": target_id, "role": "client"}, "guest_merge", f"projects:{moved}")
    return moved


@api.post("/auth/login")
def login(body: LoginIn, request: Request):
    login_name = body.login.strip().lower()
    ip = client_ip(request)
    keys = [(f"login|{ip}|{login_name}", LOGIN_LIMIT), (f"ip|{ip}", IP_LIMIT)]
    throttle_check(keys)
    with D.db() as c:
        u = c.execute("SELECT * FROM users WHERE login=?", (login_name,)).fetchone()
        if not u or not S.verify_password(body.password, u["pass_hash"] or ""):
            D.audit(c, None, "login", login_name[:64], "denied")
            throttle_fail(keys)
            raise err(401, "bad_credentials", "Неверный логин или пароль")
        throttle_clear(keys[0][0])
        D.audit(c, dict(u), "login", "", "ok")
        merged = merge_guest(c, body.merge_token, u["id"]) if u["role"] == "client" else 0
        return {"token": start_session(c, u["id"]), "user": user_public(u), "merged_projects": merged}


class RegisterIn(BaseModel):
    login: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(default="", max_length=60)
    merge_token: Optional[str] = Field(default=None, max_length=200)


@api.post("/auth/register")
def register(body: RegisterIn, request: Request):
    """Создание аккаунта. Если передан токен гостя, его проекты и заказы переходят в новый аккаунт."""
    ip = client_ip(request)
    keys = [(f"reg|{ip}", 20)]
    throttle_check(keys)
    login_name = body.login.strip().lower()
    with D.db() as c:
        if c.execute("SELECT 1 FROM users WHERE login=?", (login_name,)).fetchone():
            throttle_fail(keys)
            raise err(409, "login_taken", "Этот логин уже занят. Придумайте другой")
        cur = c.execute("INSERT INTO users(login,pass_hash,role,name,created_at) VALUES(?,?,'client',?,?)",
                        (login_name, S.hash_password(body.password), (body.name.strip() or login_name)[:60], D.now_iso()))
        u = c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        D.audit(c, dict(u), "register", "", "ok")
        merged = merge_guest(c, body.merge_token, u["id"])
        throttle_fail(keys)  # считаем и успешные регистрации: защита от массового создания аккаунтов
        return {"token": start_session(c, u["id"]), "user": user_public(u), "merged_projects": merged}


@api.post("/auth/guest")
def guest():
    with D.db() as c:
        cur = c.execute("INSERT INTO users(login,role,name,created_at) VALUES(NULL,'client','Гость',?)", (D.now_iso(),))
        u = c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        D.audit(c, dict(u), "guest_login", "", "ok")
        return {"token": start_session(c, u["id"]), "user": user_public(u)}


@api.post("/auth/telegram")
def telegram_login(body: TgIn):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise err(501, "telegram_disabled", "Вход через Telegram не настроен")
    tg = S.verify_telegram_init_data(body.init_data, token)
    if not tg or "id" not in tg:
        with D.db() as c:
            D.audit(c, None, "telegram_login", "", "denied", "bad signature")
        raise err(401, "bad_signature", "Подпись Telegram не прошла проверку")
    with D.db() as c:
        u = c.execute("SELECT * FROM users WHERE tg_id=?", (str(tg["id"]),)).fetchone()
        if not u:
            name = (tg.get("first_name") or "Пользователь")[:60]
            cur = c.execute("INSERT INTO users(role,name,tg_id,created_at) VALUES('client',?,?,?)",
                            (name, str(tg["id"]), D.now_iso()))
            u = c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        D.audit(c, dict(u), "telegram_login", "", "ok")
        out = {"token": start_session(c, u["id"]), "user": user_public(u)}
        if u["role"] == "client":
            out["home"] = {"projects": projects_list(c, u["id"]), "orders": orders_list(c, u["id"])}
        return out


@api.post("/auth/logout")
def logout(authorization: str = Header(default=""), user: dict = Depends(current_user)):
    with D.db() as c:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (S.sha(authorization[7:]),))
        D.audit(c, user, "logout")
    return {"ok": True}


@api.get("/me")
def me(user: dict = Depends(current_user)):
    return user_public(user)


@api.delete("/me/data")
def delete_my_data(user: dict = Depends(current_user)):
    """Удаление аккаунта и всех тестовых данных пользователя (МП-06)."""
    if user["role"] == "operator":
        raise err(403, "forbidden", "Служебную учётную запись удалить нельзя")
    with D.db() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM photos WHERE user_id=?", (user["id"],))]
        D.audit(c, user, "delete_account", f"user:{user['id']}", "ok", f"photos={len(ids)}")
        c.execute("DELETE FROM users WHERE id=?", (user["id"],))
    remove_files(ids)
    for x in ai_recent(user["id"]):  # и созданные, но не добавленные в альбом AI-кадры
        ai_drop(x["tmp_id"])
    return {"ok": True, "deleted_photos": len(ids)}


# ---------- push-уведомления (Web Push, PWA и браузер) ----------
class PushKeysIn(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class PushSubIn(BaseModel):
    endpoint: str = Field(max_length=1000)
    keys: PushKeysIn


@api.get("/push/config")
def push_config(user: dict = Depends(current_user)):
    k = WP.keys() if WP.enabled() else None
    with D.db() as c:
        n = WP.count(c, user["id"])
    return {"enabled": bool(k), "public_key": k["public"] if k else None, "subscriptions": n,
            "telegram": bool(user.get("tg_id")) and TG.enabled()}


@api.post("/push/subscribe")
def push_subscribe(body: PushSubIn, request: Request, user: dict = Depends(current_user)):
    if not WP.enabled():
        raise err(503, "push_disabled", "Уведомления на сервере выключены")
    if not body.endpoint.startswith("https://"):
        raise err(422, "bad_endpoint", "Некорректная подписка браузера")
    with D.db() as c:
        WP.save(c, user["id"], {"endpoint": body.endpoint, "keys": body.keys.model_dump()},
                request.headers.get("user-agent", ""))
        D.audit(c, user, "push_subscribe", f"user:{user['id']}")
        n = WP.count(c, user["id"])
    return {"ok": True, "subscriptions": n}


@api.delete("/push/subscribe")
def push_unsubscribe(endpoint: Optional[str] = Query(default=None, max_length=1000), user: dict = Depends(current_user)):
    with D.db() as c:
        n = WP.remove(c, user["id"], endpoint)
        D.audit(c, user, "push_unsubscribe", f"user:{user['id']}", "ok", f"removed={n}")
    return {"ok": True, "removed": n}


@api.post("/push/test")
def push_test(user: dict = Depends(current_user)):
    """Пробное уведомление себе: показывает, дошло ли сообщение до push-сервиса браузера."""
    if not WP.enabled():
        raise err(503, "push_disabled", "Уведомления на сервере выключены")
    r = WP.send_to_user(user["id"], "picPac: уведомления работают", "Так мы сообщим о смене статуса заказа.", "/#/orders")
    if not r["sent"] and not r["failed"]:
        raise err(409, "no_subscription", "На этом устройстве уведомления ещё не включены")
    return r


# ---------- каталог ----------
def get_product(c, pid: str) -> dict:
    row = c.execute("SELECT data FROM products WHERE id=?", (pid,)).fetchone()
    if not row:
        raise err(404, "no_product", "Продукт не найден")
    return json.loads(row["data"])


@api.get("/products")
def products():
    with D.db() as c:
        return [json.loads(r["data"]) for r in c.execute("SELECT data FROM products ORDER BY rowid")]


def _price_from(p: dict) -> int:
    return min(sz["base"] for sz in p["options"]["sizes"])


@api.get("/templates")
def templates():
    """Шаблоны по поводам (mock API): продукт, оформление, цена «от». Карточки каталога строятся из этого ответа."""
    with D.db() as c:
        prods = {json.loads(r["data"])["id"]: json.loads(r["data"]) for r in c.execute("SELECT data FROM products")}
    out = []
    for t in C.TEMPLATES:
        p = prods.get(t["product_id"])
        if p:
            out.append({**t, "product_title": p["title"], "kind": p["kind"], "price_from": _price_from(p),
                        "currency": "BYN", "min_photos": p["min_photos"], "production_days": p["options"].get("production_days")})
    return out


class AskIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)


class EditCmdIn(BaseModel):
    text: str = Field(min_length=2, max_length=300)


@api.post("/edit-command")
def edit_command(body: EditCmdIn, user: dict = Depends(current_user)):
    """Правка фото словами: текст -> операции редактора (см. backend/editcmd.py). Фото в модель не отправляется."""
    res = EC.parse(body.text)
    with D.db() as c:
        D.audit(c, user, "edit_command", "editor", "ok", f"engine={res['engine']} ops={','.join(o['op'] for o in res['ops'])}")
    return res


@api.post("/assistant")
def assistant(body: AskIn, user: dict = Depends(current_user)):
    """ИИ-помощник: демо-провайдер по умолчанию, Mary — при наличии MARY_API_URL/MARY_API_KEY (см. backend/assistant.py)."""
    with D.db() as c:
        prods = [json.loads(r["data"]) for r in c.execute("SELECT data FROM products ORDER BY rowid")]
        byid = {p["id"]: p for p in prods}
        draft = None
        for r in c.execute("SELECT * FROM projects WHERE user_id=? AND status='draft' ORDER BY updated_at DESC LIMIT 1", (user["id"],)):
            p = byid.get(r["product_id"])
            if p:
                draft = {"id": r["id"], "title": r["title"], "product": p["title"], "min_photos": p["min_photos"],
                         "photos": len(photos_of(c, r["id"], True))}
        orders = [{"id": r["id"], "number": f"PP-{r['id']:06d}", "status": r["status"], "total": r["total_kop"]}
                  for r in c.execute("SELECT id, status, total_kop FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 3", (user["id"],))]
        tp = templates()
        ctx = {"draft": draft, "orders": orders, "templates": [{"title": t["title"], "price_from": t["price_from"]} for t in tp],
               "products": [{"title": p["title"], "from": _price_from(p)} for p in prods]}
        res = AS.ask(body.message, ctx)
        D.audit(c, user, "assistant", "assistant", "ok", f"provider={res['provider']} len={len(body.message)}")
    return res


@api.get("/pickup-points")
def pickup_points(lat: Optional[float] = None, lon: Optional[float] = None):
    pts = [dict(p) for p in C.PICKUP_POINTS]
    if lat is not None and lon is not None:
        for p in pts:
            dx = (p["lon"] - lon) * 111.32 * math.cos(math.radians(lat))
            dy = (p["lat"] - lat) * 110.57
            p["distance_km"] = round(math.hypot(dx, dy), 1)
        pts.sort(key=lambda p: p["distance_km"])
    return pts


class PriceIn(BaseModel):
    product_id: str
    settings: dict = {}
    project_id: Optional[str] = None
    promo: Optional[str] = None
    delivery: str = "pvz"
    n_photos: Optional[int] = Field(default=None, ge=0, le=MAX_PHOTOS)


@api.post("/price")
def price(body: PriceIn, user: dict = Depends(current_user)):
    with D.db() as c:
        product = get_product(c, body.product_id)
        n = body.n_photos or 0
        if body.project_id:
            row = own_project(c, body.project_id, user)
            n = c.execute("SELECT COUNT(*) FROM photos WHERE project_id=?", (row["id"],)).fetchone()[0]
    try:
        return C.compute_price(product, body.settings, n, body.promo, body.delivery)
    except C.PricingError as exc:
        raise err(422, "bad_options", str(exc))


# ---------- проекты и фото ----------
def own_project(c, pid: str, user: dict):
    row = c.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, user["id"])).fetchone()
    if not row:  # чужой проект неотличим от несуществующего (защита от перебора id)
        raise err(404, "not_found", "Проект не найден")
    return row


def photos_of(c, pid: str, included_only: bool = False) -> list:
    """included_only=True: только фото «в книге» (без убранных пользователем; сами файлы при этом не удаляются)."""
    rows = [dict(r) for r in c.execute("SELECT * FROM photos WHERE project_id=? ORDER BY position, created_at", (pid,))]
    return [r for r in rows if not r.get("excluded")] if included_only else rows


def signed(file_id: str, size: str, version: int = 0) -> str:
    # срок округлён до часа, поэтому адрес не меняется между запросами и браузер берёт картинку из кэша;
    # версия в адресе сбрасывает кэш после правок
    exp = (int(time.time()) // 3600 + 2) * 3600
    return f"{PUBLIC_BASE_URL}/api/files/{file_id}/{size}?exp={exp}&sig={S.sign_file(file_id, size, exp)}&v={version}"


def photo_json(ph: dict) -> dict:
    edits = json.loads(ph["edits"]) if ph.get("edits") else {}
    return {"id": ph["id"], "url": signed(ph["id"], "main", ph["version"]), "thumb": signed(ph["id"], "thumb", ph["version"]),
            "proxy": signed(ph["id"], "proxy", ph["version"]),
            "orig": signed(ph["id"], "orig", 0), "width": ph["width"], "height": ph["height"],
            "name": ph["orig_name"], "taken_at": ph["taken_at"], "version": ph["version"],
            "pinned": bool(ph.get("pinned")), "excluded": bool(ph.get("excluded")),
            "edits": edits, "has_mask": os.path.exists(photo_paths(ph["id"])["mask"]),
            "upscaled": os.path.exists(photo_paths(ph["id"])["up"]),
            "retouched": os.path.exists(photo_paths(ph["id"])["retouch"])}


def project_json(c, row) -> dict:
    product = get_product(c, row["product_id"])
    photos = photos_of(c, row["id"])
    return {"id": row["id"], "title": row["title"], "status": row["status"], "product": product,
            "settings": json.loads(row["settings"]), "layout": json.loads(row["layout"]) if row["layout"] else None,
            "photos": [photo_json(p) for p in photos], "updated_at": row["updated_at"]}


def touch(c, pid: str):
    c.execute("UPDATE projects SET updated_at=? WHERE id=?", (D.now_iso(), pid))


def regen_layout(c, row, product: dict, sort: str, theme: str = ""):
    if product["kind"] == "print":
        return None
    photos = photos_of(c, row["id"], True)
    settings = json.loads(row["settings"])
    if not theme and row["layout"]:  # оформление страниц сохраняется при пересборке
        try:
            theme = json.loads(row["layout"]).get("theme", "")
        except Exception:
            theme = ""
    layout = P.build_layout(photos, settings["pages"], sort, theme or "classic") if photos else None
    if layout:
        for i, photo_id in enumerate(layout["order"]):
            c.execute("UPDATE photos SET position=? WHERE id=?", (i, photo_id))
    c.execute("UPDATE projects SET layout=? WHERE id=?", (json.dumps(layout) if layout else None, row["id"]))
    return layout


def photo_paths(photo_id: str) -> dict:
    """main — текущая версия с правками, orig — нетронутый оригинал, proxy — уменьшенная копия для предпросмотра."""
    base = os.path.join(D.upload_dir(), photo_id)
    return {"main": base + ".jpg", "thumb": base + "_t.jpg", "orig": base + "_o.jpg",
            "proxy": base + "_p.jpg", "mask": base + "_m.png", "up": base + "_u.jpg", "retouch": base + "_r.jpg"}


def remove_files(photo_ids):
    for pid in photo_ids:
        for path in photo_paths(pid).values():
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def store_new_photo(c, pid: str, user: dict, name: str, info: dict, pos: int) -> str:
    """Сохраняет файлы нового фото и строку в БД. Оригинал хранится отдельно от версии с правками."""
    photo_id = uuid.uuid4().hex
    p = photo_paths(photo_id)
    for key, data in (("main", info["main"]), ("orig", info["main"]), ("thumb", info["thumb"]), ("proxy", info["proxy"])):
        with open(p[key], "wb") as fh:
            fh.write(data)
    c.execute("INSERT INTO photos(id,project_id,user_id,orig_name,width,height,taken_at,blur,brightness,ahash,position,created_at,qx)"
              " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (photo_id, pid, user["id"], name, info["width"], info["height"], info.get("taken_at"),
               info["blur"], info["brightness"], info["ahash"], pos, D.now_iso(), P.merge_q(None, info)))
    return photo_id


def load_mask(photo_id: str):
    path = photo_paths(photo_id)["mask"]
    if os.path.exists(path):
        with Image.open(path) as m:
            return m.convert("L")
    return None


def base_path(p: dict) -> str:
    """Исходник для правок: после удаления объектов, иначе после увеличения, иначе оригинал."""
    for key in ("retouch", "up"):
        if os.path.exists(p[key]):
            return p[key]
    return p["orig"]


def ensure_original(photo_id: str):
    """Для фото, загруженных до появления редактора, оригиналом считаем текущий файл."""
    p = photo_paths(photo_id)
    if not os.path.exists(p["orig"]):
        shutil.copyfile(p["main"], p["orig"])
    if not os.path.exists(p["proxy"]):
        with Image.open(p["orig"]) as im:
            small = im.convert("RGB")
            small.thumbnail((960, 960))
            small.save(p["proxy"], "JPEG", quality=85)
    return p


class ProjectIn(BaseModel):
    product_id: str
    title: str = Field(default="Мой проект", max_length=80)


class ProjectPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=80)
    settings: Optional[dict] = None
    photo_order: Optional[list[str]] = None


@api.post("/projects")
def create_project(body: ProjectIn, user: dict = Depends(current_user)):
    with D.db() as c:
        product = get_product(c, body.product_id)
        pid = uuid.uuid4().hex
        now = D.now_iso()
        c.execute("INSERT INTO projects(id,user_id,product_id,title,settings,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                  (pid, user["id"], product["id"], body.title.strip() or "Мой проект",
                   json.dumps(C.default_settings(product)), now, now))
        D.audit(c, user, "project_create", f"project:{pid}")
        return project_json(c, own_project(c, pid, user))


def projects_list(c, user_id: int) -> list:
    out = []
    for r in c.execute("SELECT * FROM projects WHERE user_id=? ORDER BY updated_at DESC", (user_id,)):
        product = get_product(c, r["product_id"])
        photos = photos_of(c, r["id"], True)
        out.append({"id": r["id"], "title": r["title"], "status": r["status"], "product_title": product["title"],
                    "product_id": product["id"], "photos_count": len(photos), "updated_at": r["updated_at"],
                    "cover": photo_json(photos[0])["thumb"] if photos else None,
                    "min_photos": product["min_photos"],
                    "order_id": (c.execute("SELECT id FROM orders WHERE project_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
                                           (r["id"], user_id)).fetchone() or [None])[0]})
    return out


def orders_list(c, user_id: int) -> list:
    return [order_json(c, r) for r in c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()]


@api.get("/projects")
def list_projects(user: dict = Depends(current_user)):
    with D.db() as c:
        return projects_list(c, user["id"])


@api.get("/projects/{pid}")
def get_project(pid: str, user: dict = Depends(current_user)):
    with D.db() as c:
        return project_json(c, own_project(c, pid, user))


def ensure_draft(row):
    if row["status"] != "draft":
        raise err(409, "project_locked", "Проект уже оформлен в заказ и не редактируется")


@api.patch("/projects/{pid}")
def patch_project(pid: str, body: ProjectPatch, user: dict = Depends(current_user)):
    with D.db() as c:
        row = own_project(c, pid, user)
        ensure_draft(row)
        product = get_product(c, row["product_id"])
        relayout = None
        if body.title is not None:
            c.execute("UPDATE projects SET title=? WHERE id=?", (body.title.strip() or "Мой проект", pid))
        if body.settings is not None:
            merged = {**json.loads(row["settings"]), **body.settings}
            try:
                clean = C.validate_settings(product, merged)
            except C.PricingError as exc:
                raise err(422, "bad_options", str(exc))
            c.execute("UPDATE projects SET settings=? WHERE id=?", (json.dumps(clean), pid))
            relayout = "manual" if row["layout"] else None
        if body.photo_order is not None:
            ids = [p["id"] for p in photos_of(c, pid)]
            if sorted(ids) != sorted(body.photo_order):
                raise err(422, "bad_order", "Список фото не совпадает с проектом")
            for i, photo_id in enumerate(body.photo_order):
                c.execute("UPDATE photos SET position=? WHERE id=?", (i, photo_id))
            relayout = "manual" if row["layout"] else None
        if relayout:
            regen_layout(c, c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone(), product, relayout)
        touch(c, pid)
        return project_json(c, own_project(c, pid, user))


@api.post("/projects/{pid}/clone")
def clone_project(pid: str, user: dict = Depends(current_user)):
    """«Заказать ещё»: новый черновик с теми же фото, правками и параметрами. Исходный проект не меняется."""
    with D.db() as c:
        row = own_project(c, pid, user)
        new_id = uuid.uuid4().hex
        now = D.now_iso()
        c.execute("INSERT INTO projects(id,user_id,product_id,title,settings,layout,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                  (new_id, user["id"], row["product_id"], row["title"], row["settings"], None, now, now))
        for ph in photos_of(c, pid):
            nid = uuid.uuid4().hex
            src, dst = photo_paths(ph["id"]), photo_paths(nid)
            for key in src:
                if os.path.exists(src[key]):
                    shutil.copyfile(src[key], dst[key])
            cols = [k for k in ph.keys() if k not in ("id", "project_id", "created_at")]
            c.execute(f"INSERT INTO photos(id,project_id,created_at,{','.join(cols)}) VALUES(?,?,?,{','.join('?' * len(cols))})",
                      (nid, new_id, now, *[ph[k] for k in cols]))
        new_row = own_project(c, new_id, user)
        regen_layout(c, new_row, get_product(c, row["product_id"]), "manual")
        D.audit(c, user, "project_clone", f"project:{new_id}", "ok", f"from={pid}")
        return project_json(c, own_project(c, new_id, user))


@api.delete("/projects/{pid}")
def delete_project(pid: str, user: dict = Depends(current_user)):
    with D.db() as c:
        row = own_project(c, pid, user)
        ensure_draft(row)
        ids = [p["id"] for p in photos_of(c, pid)]
        c.execute("DELETE FROM projects WHERE id=?", (pid,))
        D.audit(c, user, "project_delete", f"project:{pid}")
    remove_files(ids)
    return {"ok": True}


@api.post("/projects/{pid}/photos")
async def upload_photos(pid: str, files: list[UploadFile] = File(...), user: dict = Depends(current_user)):
    with D.db() as c:
        row = own_project(c, pid, user)
        ensure_draft(row)
        count = c.execute("SELECT COUNT(*) FROM photos WHERE project_id=?", (pid,)).fetchone()[0]
        pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM photos WHERE project_id=?", (pid,)).fetchone()[0]
    added, rejected = [], []
    for f in files:
        name = (f.filename or "photo")[:120]
        data = await f.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            rejected.append({"name": name, "reason": "Файл больше 15 МБ"})
            continue
        if count + len(added) >= MAX_PHOTOS:
            rejected.append({"name": name, "reason": f"В проекте не больше {MAX_PHOTOS} фото"})
            continue
        try:
            info = P.analyze_image(data)
        except P.ImageRejected as exc:
            rejected.append({"name": name, "reason": str(exc)})
            continue
        nsfw = await asyncio.to_thread(M.score, info["thumb"])  # локально, фото никуда не уходит
        if nsfw is not None:
            info["q"]["nsfw"] = nsfw
        with D.db() as c:
            photo_id = store_new_photo(c, pid, user, name, info, pos)
            added.append(photo_id)
            if M.flagged(info["q"]):
                D.audit(c, user, "moderation_flag", f"photo:{photo_id}", "flag", f"score={nsfw}")
        pos += 1
    with D.db() as c:
        row = own_project(c, pid, user)
        product = get_product(c, row["product_id"])
        if added and row["layout"]:
            regen_layout(c, row, product, "manual")
        touch(c, pid)
        D.audit(c, user, "photos_upload", f"project:{pid}", "ok", f"added={len(added)} rejected={len(rejected)}")
        return {"added": len(added), "rejected": rejected, "project": project_json(c, own_project(c, pid, user))}


class PhotoFlags(BaseModel):
    pinned: Optional[bool] = None
    excluded: Optional[bool] = None


@api.patch("/projects/{pid}/photos/{photo_id}/flags")
def photo_flags(pid: str, photo_id: str, body: PhotoFlags, user: dict = Depends(current_user)):
    """Smart Story: закрепить кадр (своя страница), убрать фото из книги или вернуть его. Файлы не удаляются."""
    with D.db() as c:
        row = own_project(c, pid, user)
        ensure_draft(row)
        ph = c.execute("SELECT * FROM photos WHERE id=? AND project_id=?", (photo_id, pid)).fetchone()
        if not ph:
            raise err(404, "not_found", "Фото не найдено")
        pinned, excluded = bool(ph["pinned"]), bool(ph["excluded"])
        if body.excluded is not None:
            excluded = body.excluded
            if excluded:
                pinned = False  # убранное фото не может быть закреплённым
        if body.pinned is not None:
            pinned = body.pinned
            if pinned:
                excluded = False
        c.execute("UPDATE photos SET pinned=?, excluded=? WHERE id=?", (int(pinned), int(excluded), photo_id))
        if row["layout"]:
            sort = json.loads(row["layout"]).get("sort", "manual")
            regen_layout(c, row, get_product(c, row["product_id"]), sort)
        touch(c, pid)
        D.audit(c, user, "photo_flags", f"photo:{photo_id}", "ok", f"pinned={int(pinned)} excluded={int(excluded)}")
        return project_json(c, own_project(c, pid, user))


@api.delete("/projects/{pid}/photos/{photo_id}")
def delete_photo(pid: str, photo_id: str, user: dict = Depends(current_user)):
    with D.db() as c:
        row = own_project(c, pid, user)
        ensure_draft(row)
        if not c.execute("SELECT 1 FROM photos WHERE id=? AND project_id=?", (photo_id, pid)).fetchone():
            raise err(404, "not_found", "Фото не найдено")
        c.execute("DELETE FROM photos WHERE id=?", (photo_id,))
        if row["layout"]:
            regen_layout(c, row, get_product(c, row["product_id"]), "manual")
        touch(c, pid)
        D.audit(c, user, "photo_delete", f"photo:{photo_id}")
        out = project_json(c, own_project(c, pid, user))
    remove_files([photo_id])
    return out


def own_photo(c, pid: str, photo_id: str, user: dict):
    own_project(c, pid, user)
    row = c.execute("SELECT * FROM photos WHERE id=? AND project_id=?", (photo_id, pid)).fetchone()
    if not row:
        raise err(404, "not_found", "Фото не найдено")
    return dict(row)


def apply_edits(c, photo: dict, edits: dict) -> dict:
    """Рисует правки поверх оригинала и обновляет текущую версию и метрики качества. Пустые правки = возврат к оригиналу."""
    e = I.sanitize(edits)
    p = ensure_original(photo["id"])
    with Image.open(base_path(p)) as im:
        orig = im.convert("RGB")
    out = I.render(orig, e, load_mask(photo["id"]) if "bg" in e else None)
    info = P.encode_variants(out)
    for key in ("main", "thumb"):
        with open(p[key], "wb") as fh:
            fh.write(info[key])
    c.execute("UPDATE photos SET width=?,height=?,blur=?,brightness=?,ahash=?,qx=?,version=version+1,edits=? WHERE id=?",
              (info["width"], info["height"], info["blur"], info["brightness"], info["ahash"],
               P.merge_q(photo.get("qx"), info), json.dumps(e) if e else None, photo["id"]))
    return e


class EditIn(BaseModel):
    edits: dict = {}
    size: int = Field(default=800, ge=64, le=1200)


@api.post("/projects/{pid}/photos/{photo_id}/preview")
def preview_edits(pid: str, photo_id: str, body: EditIn, user: dict = Depends(current_user)):
    """Быстрый предпросмотр правок на уменьшенной копии. Ничего не сохраняет."""
    with D.db() as c:
        photo = own_photo(c, pid, photo_id, user)
    p = ensure_original(photo_id)
    e = I.sanitize(body.edits)
    with Image.open(p["proxy"]) as im:
        proxy = im.convert("RGB")
    mask = load_mask(photo_id) if "bg" in e else None
    out = I.render(proxy, e, mask)
    out.thumbnail((body.size, body.size))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=82)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@api.put("/projects/{pid}/photos/{photo_id}/edit")
def save_edits(pid: str, photo_id: str, body: EditIn, user: dict = Depends(current_user)):
    with D.db() as c:
        row = own_project(c, pid, user)
        ensure_draft(row)
        photo = own_photo(c, pid, photo_id, user)
        e = apply_edits(c, photo, body.edits)
        touch(c, pid)
        D.audit(c, user, "photo_edit" if e else "photo_revert", f"photo:{photo_id}", "ok", ",".join(sorted(e)))
        return project_json(c, own_project(c, pid, user))


@api.post("/projects/{pid}/photos/{photo_id}/enhance")
def enhance_photo(pid: str, photo_id: str, user: dict = Depends(current_user)):
    """Автоулучшение как обычная обратимая правка."""
    with D.db() as c:
        row = own_project(c, pid, user)
        ensure_draft(row)
        photo = own_photo(c, pid, photo_id, user)
        edits = json.loads(photo["edits"]) if photo["edits"] else {}
        edits["auto"] = True
        apply_edits(c, photo, edits)
        touch(c, pid)
        D.audit(c, user, "photo_enhance", f"photo:{photo_id}")
        return project_json(c, own_project(c, pid, user))


@api.get("/capabilities")
def capabilities():
    from . import bgremove
    from . import inpaint
    from . import upscale

    return {"moderation": M.available(), "bg_removal": bgremove.available(), "upscale": upscale.available(), "object_removal": inpaint.available(), "ai_generate": G.available() or AI_GENERATE,
            "ai_local": G.available(),
            "ai_provider": ("Локальная модель на сервере picPac (наружу ничего не уходит)" if G.available()
                            else "Pollinations (только текст запроса)" if AI_GENERATE else None)}


def _rebuild_base(c, photo: dict, base_img: Image.Image):
    """Пересобирает рабочие файлы после увеличения или отмены: версия с правками, миниатюра и копия для предпросмотра."""
    p = photo_paths(photo["id"])
    edits = I.sanitize(json.loads(photo["edits"])) if photo["edits"] else {}
    out = I.render(base_img, edits, load_mask(photo["id"]) if "bg" in edits else None)
    info = P.encode_variants(out)
    for key in ("main", "thumb"):
        with open(p[key], "wb") as fh:
            fh.write(info[key])
    small = base_img.copy()
    small.thumbnail((960, 960))
    small.save(p["proxy"], "JPEG", quality=85)
    q = json.loads(P.merge_q(photo.get("qx"), info))
    q["enh"] = os.path.exists(p["up"])  # улучшено нейросетью: проверка размытия по миниатюре уже не показательна
    c.execute("UPDATE photos SET width=?,height=?,blur=?,brightness=?,ahash=?,qx=?,version=version+1 WHERE id=?",
              (info["width"], info["height"], info["blur"], info["brightness"], info["ahash"],
               json.dumps(q), photo["id"]))


class UpscaleIn(BaseModel):
    scale: int = Field(default=2, ge=2, le=4)


@api.post("/projects/{pid}/photos/{photo_id}/upscale")
def upscale_photo(pid: str, photo_id: str, body: UpscaleIn, user: dict = Depends(current_user)):
    """Нейросетевое увеличение и улучшение чёткости (Real-ESRGAN, локально). Оригинал не меняется, можно отменить."""
    from . import upscale

    if body.scale not in (2, 4):
        raise err(422, "bad_scale", "Увеличение возможно в 2 или 4 раза")
    if not upscale.available():
        raise err(501, "no_upscale", "Улучшение качества недоступно в этой сборке (нет модели, см. README)")
    with D.db() as c:
        ensure_draft(own_project(c, pid, user))
        photo = own_photo(c, pid, photo_id, user)
    p = ensure_original(photo_id)
    if os.path.exists(p["retouch"]):
        raise err(409, "retouched", "Сначала верните фото после удаления объектов: улучшайте качество до ретуши, а не после")
    with Image.open(p["orig"]) as im:
        src = im.convert("RGB")
    if max(src.size) > upscale.MAX_IN_SIDE:
        raise err(422, "already_big", "В этом фото больше 3200 px по длинной стороне: для печати до A3 этого хватает, увеличивать не нужно. "
                                          "Если оно размыто, используйте «Автоулучшение» или замените снимок")
    scale = body.scale
    while scale > 2 and max(src.size) * scale > upscale.MAX_OUT_SIDE:
        scale = 2
    try:
        big = upscale.run(src, scale)
    except upscale.Busy:
        raise err(429, "busy", "Сейчас улучшается другое фото. Повторите через несколько секунд")
    big.save(p["up"], "JPEG", quality=95)
    with D.db() as c:
        photo = own_photo(c, pid, photo_id, user)
        _rebuild_base(c, photo, big)
        touch(c, pid)
        D.audit(c, user, "photo_upscale", f"photo:{photo_id}", "ok", f"x{scale}")
        return project_json(c, own_project(c, pid, user))


@api.get("/projects/{pid}/photos/{photo_id}/upscale-compare")
def upscale_compare(pid: str, photo_id: str, user: dict = Depends(current_user)):
    """Один и тот же участок кадра до и после улучшения в натуральном масштабе: на экране телефона целиком кадр разницу скрывает."""
    import base64

    with D.db() as c:
        own_photo(c, pid, photo_id, user)
    p = photo_paths(photo_id)
    if not os.path.exists(p["up"]) or not os.path.exists(p["orig"]):
        raise err(404, "no_upscale", "Фото не увеличивалось")
    with Image.open(p["orig"]) as o, Image.open(p["up"]) as u:
        o, u = o.convert("RGB"), u.convert("RGB")
        k = u.width / o.width
        side = max(48, min(o.width, o.height) // 6)
        cx, cy = o.width // 2, o.height // 2
        box0 = (cx - side // 2, cy - side // 2, cx + side // 2, cy + side // 2)
        box1 = tuple(round(v * k) for v in box0)
        def enc(im):
            buf = io.BytesIO()
            im.resize((320, 320), Image.BICUBIC).save(buf, "JPEG", quality=88)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        return {"before": enc(o.crop(box0)), "after": enc(u.crop(box1)), "scale": round(k, 2),
                "w0": o.width, "h0": o.height, "w1": u.width, "h1": u.height}


@api.delete("/projects/{pid}/photos/{photo_id}/upscale")
def undo_upscale(pid: str, photo_id: str, user: dict = Depends(current_user)):
    with D.db() as c:
        ensure_draft(own_project(c, pid, user))
        photo = own_photo(c, pid, photo_id, user)
    p = ensure_original(photo_id)
    if os.path.exists(p["retouch"]):
        raise err(409, "retouched", "Сначала верните фото после удаления объектов")
    if os.path.exists(p["up"]):
        os.remove(p["up"])
        with Image.open(p["orig"]) as im:
            base_img = im.convert("RGB")
        with D.db() as c:
            _rebuild_base(c, photo, base_img)
            touch(c, pid)
            D.audit(c, user, "photo_upscale_undo", f"photo:{photo_id}")
    with D.db() as c:
        return project_json(c, own_project(c, pid, user))


class EraseIn(BaseModel):
    mask: str = Field(max_length=4_000_000)  # PNG в base64 (data URL): белое = убрать


@api.post("/projects/{pid}/photos/{photo_id}/erase")
def erase_object(pid: str, photo_id: str, body: EraseIn, user: dict = Depends(current_user)):
    """Удаление объекта: нейросеть LaMa достраивает фон на месте закрашенного. Оригинал не меняется, можно вернуть."""
    import base64

    from . import inpaint

    if not inpaint.available():
        raise err(501, "no_inpaint", "Удаление объектов недоступно в этой сборке (нет модели, см. README)")
    with D.db() as c:
        ensure_draft(own_project(c, pid, user))
        photo = own_photo(c, pid, photo_id, user)
    try:
        raw = base64.b64decode(body.mask.split(",", 1)[-1], validate=False)
        with Image.open(io.BytesIO(raw)) as m:
            m.load()
            mask = (m.getchannel("A") if "A" in m.getbands() else m.convert("L")).copy()
    except Exception:
        raise err(422, "bad_mask", "Не удалось прочитать закрашенную область")
    p = ensure_original(photo_id)
    with Image.open(base_path(p)) as im:
        src = im.convert("RGB")
    mask = mask.resize(src.size, Image.BILINEAR).point(lambda v: 255 if v > 100 else 0)
    share = sum(mask.resize((64, 64), Image.BOX).histogram()[128:]) / 4096
    if share == 0:
        raise err(422, "empty_mask", "Закрасьте кистью то, что нужно убрать")
    if share > 0.35:
        raise err(422, "mask_too_big", "Закрашена слишком большая часть кадра. Уберите объекты по одному")
    try:
        out = inpaint.erase(src, mask)
    except inpaint.Busy:
        raise err(429, "busy", "Сейчас обрабатывается другое фото. Повторите через несколько секунд")
    out.save(p["retouch"], "JPEG", quality=95)
    with D.db() as c:
        photo = own_photo(c, pid, photo_id, user)
        _rebuild_base(c, photo, out)
        touch(c, pid)
        D.audit(c, user, "photo_erase", f"photo:{photo_id}")
        return project_json(c, own_project(c, pid, user))


@api.delete("/projects/{pid}/photos/{photo_id}/erase")
def undo_erase(pid: str, photo_id: str, user: dict = Depends(current_user)):
    with D.db() as c:
        ensure_draft(own_project(c, pid, user))
        photo = own_photo(c, pid, photo_id, user)
    p = ensure_original(photo_id)
    if os.path.exists(p["retouch"]):
        os.remove(p["retouch"])
        with Image.open(base_path(p)) as im:
            base_img = im.convert("RGB")
        with D.db() as c:
            _rebuild_base(c, photo, base_img)
            touch(c, pid)
            D.audit(c, user, "photo_erase_undo", f"photo:{photo_id}")
    with D.db() as c:
        return project_json(c, own_project(c, pid, user))


@api.post("/projects/{pid}/photos/{photo_id}/bg-mask")
def make_bg_mask(pid: str, photo_id: str, user: dict = Depends(current_user)):
    from . import bgremove

    if not bgremove.available():
        raise err(501, "no_bgremove", "Удаление фона недоступно в этой сборке (нет модели)")
    with D.db() as c:
        ensure_draft(own_project(c, pid, user))
        own_photo(c, pid, photo_id, user)
    p = ensure_original(photo_id)
    with Image.open(p["orig"]) as im:
        mask = bgremove.make_mask(im.convert("RGB"))
    mask.save(p["mask"])
    with D.db() as c:
        D.audit(c, user, "bg_mask", f"photo:{photo_id}")
        return project_json(c, own_project(c, pid, user))


# ---------- «AI-студия»: генерация кадра по описанию ----------
AI_GENERATE = os.environ.get("AI_GENERATE", "1") == "1"
_last_generate: dict = {}


AI_KEEP = 12  # сколько последних созданных кадров храним на пользователя, пока их не добавили в альбом


def fetch_generated(prompt: str) -> bytes:
    """Запасной вариант без локальной модели: бесплатный сервис Pollinations. Наружу уходит ТОЛЬКО текст запроса."""
    r = httpx.get("https://image.pollinations.ai/prompt/" + quote(prompt),
                  params={"width": 1024, "height": 1024, "nologo": "true", "seed": int(time.time()) % 100000},
                  timeout=60, follow_redirects=True)
    if r.status_code == 402 or r.status_code == 429:
        wait = re.search(r"Retry after (\d+)s", r.text)
        raise err(429, "provider_limit", "Бесплатный сервис генерации принимает один запрос в 45 секунд. "
                  f"Повторите через {wait.group(1) if wait else 45} с")
    if r.status_code != 200 or "image" not in r.headers.get("content-type", ""):
        raise RuntimeError(f"provider status {r.status_code}")
    return r.content


class GenerateIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=240)
    style: str = "photo"


def ai_meta_path(tmp_id: str) -> str:
    return os.path.join(D.ai_tmp_dir(), tmp_id + ".json")


def ai_recent(user_id: int) -> list:
    """Созданные, но ещё не добавленные в альбом кадры пользователя (новые сверху). Хранятся на сервере, поэтому не
    теряются, если закрыть приложение."""
    d = D.ai_tmp_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for name in os.listdir(d):
        if not (name.startswith(f"{user_id}_") and name.endswith(".jpg")):
            continue
        tmp_id = name[:-4]
        meta = {}
        try:
            with open(ai_meta_path(tmp_id), encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            pass
        out.append({"tmp_id": tmp_id, "url": signed(tmp_id, "ai"), "prompt": meta.get("prompt", ""),
                    "style": meta.get("style", ""), "created_at": meta.get("created_at", ""),
                    "_mtime": os.path.getmtime(os.path.join(d, name))})
    out.sort(key=lambda x: x["_mtime"], reverse=True)
    for x in out:
        x.pop("_mtime")
    return out


def ai_drop(tmp_id: str):
    for path in (os.path.join(D.ai_tmp_dir(), tmp_id + ".jpg"), ai_meta_path(tmp_id)):
        try:
            os.remove(path)
        except OSError:
            pass


@api.post("/projects/{pid}/ai/generate")
def ai_generate(pid: str, body: GenerateIn, user: dict = Depends(current_user)):
    local = G.available()
    if not local and not AI_GENERATE:
        raise err(501, "ai_off", "Генерация отключена администратором")
    with D.db() as c:
        ensure_draft(own_project(c, pid, user))
    if time.time() - _last_generate.get(user["id"], 0) < 4:
        raise err(429, "too_fast", "Подождите несколько секунд перед следующим запросом")
    _last_generate[user["id"]] = time.time()
    prompt = "".join(ch for ch in body.prompt if ch.isprintable()).strip()
    style = body.style if body.style in G.STYLE else "photo"
    try:
        if local:
            img, en = G.generate(prompt, style)
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=93)
            raw = buf.getvalue()
        else:
            full, en, _neg = G.build_prompt(prompt, style)
            raw = fetch_generated(full)
        info = P.analyze_image(raw)
    except G.Busy:
        raise err(429, "busy", "Сейчас много желающих: очередь на создание кадров слишком длинная. Повторите через минуту")
    except G.Blocked:
        with D.db() as c:
            D.audit(c, user, "ai_generate", f"project:{pid}", "blocked", "content filter")
        raise err(422, "content_blocked", "Фильтр контента не пропустил результат. Измените описание")
    except (httpx.HTTPError, RuntimeError, P.ImageRejected):
        with D.db() as c:
            D.audit(c, user, "ai_generate", f"project:{pid}", "failed")
        raise err(502, "provider_error", "Не удалось создать кадр. Попробуйте ещё раз или добавьте свои фото")
    tmp_id = f"{user['id']}_{uuid.uuid4().hex}"
    os.makedirs(D.ai_tmp_dir(), exist_ok=True)
    with open(os.path.join(D.ai_tmp_dir(), tmp_id + ".jpg"), "wb") as fh:
        fh.write(info["main"])
    with open(ai_meta_path(tmp_id), "w", encoding="utf-8") as fh:
        json.dump({"prompt": prompt, "prompt_en": en, "style": style, "project_id": pid, "created_at": D.now_iso(),
                   "engine": "local" if local else "pollinations"}, fh, ensure_ascii=False)
    for old in ai_recent(user["id"])[AI_KEEP:]:  # храним только последние кадры
        ai_drop(old["tmp_id"])
    with D.db() as c:
        D.audit(c, user, "ai_generate", f"project:{pid}", "ok",
                f"prompt_len={len(prompt)} style={style} engine={'local' if local else 'pollinations'}")
    return {"tmp_id": tmp_id, "url": signed(tmp_id, "ai"), "width": info["width"], "height": info["height"],
            "prompt": prompt, "prompt_en": en}


@api.get("/projects/{pid}/ai/recent")
def ai_recent_list(pid: str, user: dict = Depends(current_user)):
    with D.db() as c:
        own_project(c, pid, user)
    return ai_recent(user["id"])


@api.delete("/projects/{pid}/ai/recent/{tmp_id}")
def ai_recent_delete(pid: str, tmp_id: str, user: dict = Depends(current_user)):
    if not re.fullmatch(r"[0-9]+_[0-9a-f]{32}", tmp_id) or not tmp_id.startswith(f"{user['id']}_"):
        raise err(404, "not_found", "Кадр не найден")
    with D.db() as c:
        own_project(c, pid, user)
    ai_drop(tmp_id)
    return ai_recent(user["id"])


class AcceptIn(BaseModel):
    tmp_id: str = Field(pattern=r"^[0-9]+_[0-9a-f]{32}$", max_length=60)
    title: str = Field(default="AI-кадр", max_length=60)


@api.post("/projects/{pid}/ai/accept")
def ai_accept(pid: str, body: AcceptIn, user: dict = Depends(current_user)):
    if not body.tmp_id.startswith(f"{user['id']}_"):
        raise err(404, "not_found", "Кадр не найден")
    path = os.path.join(D.ai_tmp_dir(), body.tmp_id + ".jpg")
    if not os.path.exists(path):
        raise err(404, "not_found", "Кадр не найден или устарел")
    with open(path, "rb") as fh:
        info = P.analyze_image(fh.read())
    with D.db() as c:
        row = own_project(c, pid, user)
        ensure_draft(row)
        pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM photos WHERE project_id=?", (pid,)).fetchone()[0]
        photo_id = store_new_photo(c, pid, user, body.title, info, pos)
        if row["layout"]:
            regen_layout(c, row, get_product(c, row["product_id"]), "manual")
        touch(c, pid)
        D.audit(c, user, "ai_accept", f"photo:{photo_id}")
    ai_drop(body.tmp_id)
    with D.db() as c:
        return project_json(c, own_project(c, pid, user))


@api.post("/projects/{pid}/automaket")
def automaket(pid: str, sort: str = Query("date", pattern="^(date|manual)$"),
              theme: str = Query("", pattern="^(|classic|kids|travel|party|soft)$"), user: dict = Depends(current_user)):
    with D.db() as c:
        row = own_project(c, pid, user)
        ensure_draft(row)
        product = get_product(c, row["product_id"])
        if product["kind"] == "print":
            raise err(422, "no_layout", "Автомакет недоступен для обычной печати фото")
        if not photos_of(c, pid, True):
            raise err(422, "no_photos", "Сначала добавьте фотографии")
        regen_layout(c, row, product, sort, theme)
        touch(c, pid)
        D.audit(c, user, "automaket", f"project:{pid}", "ok", f"sort={sort} theme={theme}")
        return project_json(c, own_project(c, pid, user))


@api.get("/projects/{pid}/suggest-pages")
def suggest(pid: str, user: dict = Depends(current_user)):
    with D.db() as c:
        row = own_project(c, pid, user)
        product = get_product(c, row["product_id"])
        if product["kind"] != "book":
            raise err(422, "not_book", "Только для фотокниг")
        n = len(photos_of(c, pid, True))
        return {"pages": C.suggest_pages(product, n), "photos": n}


def backfill_quality(c, photos: list) -> list:
    """Фото, загруженные до появления расширенных проверок, считаем один раз по сохранённому файлу."""
    for ph in photos:
        if not ph.get("qx") and os.path.exists(photo_paths(ph["id"])["main"]):
            with Image.open(photo_paths(ph["id"])["main"]) as im:
                ph["qx"] = json.dumps(P._metrics(im.convert("RGB"))[3])
            c.execute("UPDATE photos SET qx=? WHERE id=?", (ph["qx"], ph["id"]))
    return photos


@api.get("/projects/{pid}/check")
def check(pid: str, user: dict = Depends(current_user)):
    with D.db() as c:
        row = own_project(c, pid, user)
        product = get_product(c, row["product_id"])
        res = P.check_project(product, json.loads(row["settings"]), backfill_quality(c, photos_of(c, pid, True)),
                              json.loads(row["layout"]) if row["layout"] else None)
        D.audit(c, user, "preflight", f"project:{pid}", "ok" if res["ok"] else "blocked",
                f"warnings={len(res['warnings'])}")
        return res


class CleanupIn(BaseModel):
    duplicates: bool = False
    bad: bool = False


@api.post("/projects/{pid}/cleanup")
def cleanup(pid: str, body: CleanupIn, user: dict = Depends(current_user)):
    """Убирает дубликаты и неудачные снимки из макета, сохраняя файлы для отмены."""
    with D.db() as c:
        row = own_project(c, pid, user)
        ensure_draft(row)
        product = get_product(c, row["product_id"])
        photos = backfill_quality(c, photos_of(c, pid, True))
        drop = {}
        if body.duplicates:
            for g in P.find_duplicates(photos):
                for i in g["remove"]:
                    drop[i] = "duplicate"
        if body.bad:
            for p in photos:
                if p["id"] not in drop and P.bad_reason(p):
                    drop[p["id"]] = "bad"
        for i in drop:
            c.execute("UPDATE photos SET excluded=1, pinned=0 WHERE id=? AND project_id=?", (i, pid))
        if drop and row["layout"]:
            regen_layout(c, row, product, "manual")
        touch(c, pid)
        D.audit(c, user, "photos_cleanup", f"project:{pid}", "ok",
                f"excluded_duplicates={sum(1 for v in drop.values() if v == 'duplicate')} "
                f"excluded_bad={sum(1 for v in drop.values() if v == 'bad')}")
        out = project_json(c, own_project(c, pid, user))
    return {"removed": len(drop), "duplicates": sum(1 for v in drop.values() if v == "duplicate"),
            "bad": sum(1 for v in drop.values() if v == "bad"), "project": out}


@api.get("/files/{photo_id}/{size}")
def get_file(photo_id: str, size: str, exp: int = 0, sig: str = ""):
    """Файлы отдаются только по подписанной ссылке с ограниченным сроком жизни."""
    if size not in ("main", "thumb", "orig", "ai", "proxy") or not re.fullmatch(r"[0-9a-f_]{20,70}", photo_id) \
            or not S.verify_file_sig(photo_id, size, exp, sig):
        raise err(403, "bad_link", "Ссылка недействительна или устарела")
    path = os.path.join(D.ai_tmp_dir(), photo_id + ".jpg") if size == "ai" else photo_paths(photo_id)[size]
    if size == "proxy" and not os.path.exists(path):  # старые фото без уменьшенной копии
        path = photo_paths(photo_id)["main"]
    if not os.path.exists(path):
        raise err(404, "not_found", "Файл не найден")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# ---------- заказы ----------
class DeliveryIn(BaseModel):
    method: str
    pvz_id: Optional[str] = None
    recipient_name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=7, max_length=20, pattern=r"^[0-9+()\- ]+$")
    address: Optional[str] = Field(default=None, max_length=200)


class OrderIn(BaseModel):
    project_id: str
    delivery: DeliveryIn
    promo: Optional[str] = None
    accept_warnings: bool = False


class TicketIn(BaseModel):
    text: str = Field(min_length=3, max_length=1000)


def tracking_for(row) -> Optional[dict]:
    """Демо-трек-номер службы доставки: появляется, когда заказ передан в доставку. Номер выдуманный и стабильный
    (из номера заказа), реальной службы доставки за ним нет; формат похож на почтовый (RR + 9 цифр + BY)."""
    if row["status"] not in ("ready", "completed"):
        return None
    method = json.loads(row["delivery"]).get("method", "")
    digits = f"{(row['id'] * 7919 + 100000000) % 1000000000:09d}"
    carrier = {"pvz": "Курьерская доставка «Фотопринт» (демо)", "belpost": "Белпочта (демо)", "courier": "Курьер (демо)"}.get(method, "Служба доставки (демо)")
    return {"number": f"RR{digits}BY", "carrier": carrier, "demo": True}


def order_json(c, row) -> dict:
    events = [{"status": e["status"], "label": STATUS_LABELS[e["status"]], "ts": e["ts"], "note": e["note"]}
              for e in c.execute("SELECT * FROM order_events WHERE order_id=? ORDER BY id", (row["id"],))]
    nxt = TRANSITIONS.get(row["status"], [])
    first = c.execute("SELECT id FROM photos WHERE project_id=? ORDER BY position, created_at LIMIT 1", (row["project_id"],)).fetchone()
    return {"project_id": row["project_id"], "cover": signed(first["id"], "thumb") if first else None, "id": row["id"], "number": f"PP-{row['id']:06d}", "status": row["status"],
            "status_label": STATUS_LABELS[row["status"]], "currency": "BYN", "total": row["total_kop"],
            "price": json.loads(row["price"]), "delivery": json.loads(row["delivery"]), "promo": row["promo"],
            "project_title": row["project_title"], "product_title": row["product_title"],
            "photos_count": row["photos_count"], "created_at": row["created_at"], "eta": row["eta"],
            "events": events, "next": nxt, "tracking": tracking_for(row),
            "tickets": [{"id": t["id"], "text": t["text"], "status": t["status"], "created_at": t["created_at"]}
                        for t in c.execute("SELECT * FROM tickets WHERE order_id=? ORDER BY id", (row["id"],))]}


def add_event(c, order_id: int, status: str, actor: str, note: str = ""):
    c.execute("INSERT INTO order_events(order_id,status,ts,actor,note) VALUES(?,?,?,?,?)",
              (order_id, status, D.now_iso(), actor, note))


@api.post("/orders")
def create_order(body: OrderIn, idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                 demo_fail: str = Header(default="", alias="X-Demo-Fail-After-Commit"),
                 user: dict = Depends(current_user)):
    """Повтор запроса с тем же Idempotency-Key возвращает уже созданный заказ (МП-05)."""
    key = idempotency_key.strip()
    if not 8 <= len(key) <= 80:
        raise err(400, "no_idempotency_key", "Нужен заголовок Idempotency-Key (8–80 символов)")
    with D.db() as c:
        dup = c.execute("SELECT * FROM orders WHERE user_id=? AND idem_key=?", (user["id"], key)).fetchone()
        if dup:
            D.audit(c, user, "order_create", f"order:{dup['id']}", "replay")
            out = order_json(c, dup)
            out["replayed"] = True
            return out
        proj = own_project(c, body.project_id, user)
        ensure_draft(proj)
        product = get_product(c, proj["product_id"])
        settings = json.loads(proj["settings"])
        photos = photos_of(c, proj["id"], True)
        res = P.check_project(product, settings, photos, json.loads(proj["layout"]) if proj["layout"] else None)
        if res["blocking"]:
            D.audit(c, user, "order_create", f"project:{proj['id']}", "blocked", res["blocking"][0]["code"])
            raise err(422, "project_not_ready", res["blocking"][0]["message"], blocking=res["blocking"])
        if res["warnings"] and not body.accept_warnings:
            raise err(409, "warnings_need_ack", "В проекте есть предупреждения. Исправьте их или продолжите осознанно",
                      warnings=res["warnings"])
        d = body.delivery
        if d.method not in C.DELIVERY:
            raise err(422, "bad_delivery", "Неизвестный способ получения")
        if d.method == "pvz" and d.pvz_id not in [p["id"] for p in C.PICKUP_POINTS]:
            raise err(422, "bad_delivery", "Выберите пункт выдачи")
        if d.method in ("belpost", "courier") and not (d.address or "").strip():
            raise err(422, "bad_delivery", "Укажите адрес доставки")
        try:
            price_ = C.compute_price(product, settings, len(photos), body.promo, d.method)
        except C.PricingError as exc:
            raise err(422, "bad_options", str(exc))
        if body.promo and price_["promo_error"]:
            raise err(422, "bad_promo", price_["promo_error"])
        dd = d.model_dump()
        if d.method == "pvz":
            pt = next(p for p in C.PICKUP_POINTS if p["id"] == d.pvz_id)
            dd["pvz_name"], dd["pvz_address"] = pt["name"], pt["address"]
        eta = (datetime.now(timezone.utc) + timedelta(days=price_["production_days"] + 1)).date().isoformat()
        try:
            cur = c.execute(
                "INSERT INTO orders(user_id,project_id,project_title,product_title,photos_count,idem_key,status,total_kop,"
                "price,delivery,promo,eta,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (user["id"], proj["id"], proj["title"], product["title"], len(photos), key, "accepted",
                 price_["total"], json.dumps(price_), json.dumps(dd, ensure_ascii=False),
                 price_["promo"], eta, D.now_iso()))
        except sqlite3.IntegrityError:  # гонка двух одинаковых запросов
            dup = c.execute("SELECT * FROM orders WHERE user_id=? AND idem_key=?", (user["id"], key)).fetchone()
            out = order_json(c, dup)
            out["replayed"] = True
            return out
        oid = cur.lastrowid
        add_event(c, oid, "accepted", "system")
        c.execute("UPDATE projects SET status='ordered', updated_at=? WHERE id=?", (D.now_iso(), proj["id"]))
        D.audit(c, user, "order_create", f"order:{oid}", "ok", f"total={price_['total']}")
        c.execute("INSERT INTO events(ts,user_id,name,props) VALUES(?,?,?,?)", (D.now_iso(), user["id"], "order_created", "{}"))
        out = order_json(c, c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
    if not out.get("replayed"):
        TG.notify_user(user["id"], f"Заказ {out['number']} принят. Статус будем присылать сюда.")
        WP.notify_user(user["id"], f"Заказ {out['number']} принят", "Пришлём уведомление, когда статус изменится.",
                       f"/#/order/{out['id']}")
    if DEMO_MODE and demo_fail == "1":  # имитация потери ответа: заказ уже сохранён, клиент ответа не получил
        raise err(503, "demo_connection_lost", "Связь прервалась после сохранения заказа (демо)")
    return out


@api.get("/orders")
def list_orders(user: dict = Depends(current_user)):
    with D.db() as c:
        return orders_list(c, user["id"])


def own_order(c, oid: int, user: dict):
    row = c.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (oid, user["id"])).fetchone()
    if not row:
        raise err(404, "not_found", "Заказ не найден")
    return row


@api.get("/orders/{oid}")
def get_order(oid: int, user: dict = Depends(current_user)):
    with D.db() as c:
        return order_json(c, own_order(c, oid, user))


@api.post("/orders/{oid}/tickets")
def create_ticket(oid: int, body: TicketIn, user: dict = Depends(current_user)):
    with D.db() as c:
        own_order(c, oid, user)
        c.execute("INSERT INTO tickets(order_id,user_id,text,created_at) VALUES(?,?,?,?)",
                  (oid, user["id"], body.text.strip(), D.now_iso()))
        D.audit(c, user, "ticket_create", f"order:{oid}")
        return order_json(c, own_order(c, oid, user))


# ---------- аналитика воронки ----------
class EventIn(BaseModel):
    name: str
    props: dict = {}


@api.post("/events")
def track(body: EventIn, user: dict = Depends(current_user)):
    if body.name not in [n for n, _ in FUNNEL]:
        raise err(422, "bad_event", "Неизвестное событие")
    with D.db() as c:
        c.execute("INSERT INTO events(ts,user_id,name,props) VALUES(?,?,?,?)",
                  (D.now_iso(), user["id"], body.name, json.dumps(body.props)[:300]))
    return {"ok": True}


# ---------- панель оператора ----------
@api.get("/admin/orders")
def admin_orders(status: Optional[str] = None, user: dict = Depends(operator_only)):
    with D.db() as c:
        q = "SELECT o.*, u.name AS client FROM orders o LEFT JOIN users u ON u.id=o.user_id"
        rows = c.execute(q + (" WHERE o.status=?" if status else "") + " ORDER BY o.id DESC LIMIT 200",
                         (status,) if status else ()).fetchall()
        out = []
        for r in rows:
            j = order_json(c, r)
            j["client"] = r["client"]
            # служебная пометка модерации: видит только оператор, клиенту в order_json не отдаётся
            qs = [json.loads(x["qx"] or "{}") for x in c.execute("SELECT qx FROM photos WHERE project_id=?", (r["project_id"],))] if r["project_id"] else []
            j["moderation"] = {"flagged": sum(1 for q in qs if M.flagged(q)),
                               "checked": sum(1 for q in qs if q.get("nsfw") is not None), "photos": len(qs)}
            out.append(j)
        return out


class StatusIn(BaseModel):
    to: str
    note: str = Field(default="", max_length=300)


@api.post("/admin/orders/{oid}/status")
def admin_status(oid: int, body: StatusIn, user: dict = Depends(operator_only)):
    with D.db() as c:
        row = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not row:
            raise err(404, "not_found", "Заказ не найден")
        if body.to not in TRANSITIONS.get(row["status"], []):
            D.audit(c, user, "order_status", f"order:{oid}", "denied", f"{row['status']}->{body.to}")
            raise err(409, "bad_transition", f"Нельзя перевести заказ из «{STATUS_LABELS[row['status']]}» "
                                             f"в «{STATUS_LABELS.get(body.to, body.to)}»")
        if body.to == "rejected" and not body.note.strip():
            raise err(422, "need_reason", "Укажите причину отклонения")
        c.execute("UPDATE orders SET status=? WHERE id=?", (body.to, oid))
        add_event(c, oid, body.to, f"operator:{user['id']}", body.note.strip())
        D.audit(c, user, "order_status", f"order:{oid}", "ok", f"{row['status']}->{body.to}")
        out = order_json(c, c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
    TG.notify_user(row["user_id"], f"Заказ {out['number']}: {STATUS_LABELS[body.to].lower()}."
                   + (f" Причина: {body.note.strip()}" if body.to == "rejected" else "")
                   + (f" Трек-номер (демо): {out['tracking']['number']}." if body.to == "ready" and out.get("tracking") else ""))
    WP.notify_user(row["user_id"], f"Заказ {out['number']}: {STATUS_LABELS[body.to].lower()}",
                   (f"Причина: {body.note.strip()}" if body.to == "rejected" else "Откройте заказ, чтобы посмотреть детали."),
                   f"/#/order/{oid}")
    return out


@api.get("/admin/audit")
def admin_audit(limit: int = Query(100, le=500), user: dict = Depends(operator_only)):
    with D.db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]


@api.get("/admin/tickets")
def admin_tickets(user: dict = Depends(operator_only)):
    with D.db() as c:
        return [dict(r) for r in c.execute(
            "SELECT t.*, o.id AS order_id FROM tickets t JOIN orders o ON o.id=t.order_id ORDER BY t.id DESC LIMIT 100")]


@api.get("/admin/funnel")
def admin_funnel(user: dict = Depends(operator_only)):
    with D.db() as c:
        steps = []
        for name, label in FUNNEL:
            n = c.execute("SELECT COUNT(DISTINCT user_id) FROM events WHERE name=?", (name,)).fetchone()[0]
            steps.append({"name": name, "label": label, "users": n})
        return steps


@api.get("/health")
def health():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "VERSION"), encoding="utf-8") as fh:
            version = fh.read().strip()
    except OSError:
        version = "dev"
    return {"ok": True, "version": version}


app.include_router(api)
app.include_router(PULSE.router)

_frontend = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
if os.path.isdir(_frontend):
    def _index():
        files = ("app.js", "styles.css", "index.html", "design.css", "theme.css", "motion.js", "config.js")
        ver = int(max(os.path.getmtime(os.path.join(_frontend, f)) for f in files if os.path.exists(os.path.join(_frontend, f))))
        with open(os.path.join(_frontend, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        for name in ("app.js", "motion.js", "config.js"):
            html = html.replace(f'src="{name}"', f'src="{name}?v={ver}"')
        for name in ("styles.css", "design.css", "theme.css"):
            html = html.replace(f'href="{name}"', f'href="{name}?v={ver}"')
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})  # телефон всегда спрашивает свежую страницу, а скрипты ловятся по ?v=

    app.add_api_route("/", _index, include_in_schema=False)
    app.add_api_route("/index.html", _index, include_in_schema=False)
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="web")
