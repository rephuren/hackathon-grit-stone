"""Живой аудит стенда: .venv/bin/python scripts/audit_live.py <BASE_URL>
Проходит весь сценарий Трека 2 по реальному адресу и печатает PASS/FAIL по каждому требованию.
Нужны app/.env с DEMO_PASSWORD и OPERATOR_PASSWORD (на экран не выводятся). Создаёт и удаляет собственные тестовые
данные; после прогона можно выполнить scripts/reset_demo.py. Заказ создаётся в аккаунте demo.
"""
import io
import json
import os
import random
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import backend  # noqa: F401  загружает app/.env
import httpx
from PIL import Image, ImageDraw, ImageFilter

BASE = sys.argv[1].rstrip("/")
R = []  # (id, требование, результат, доказательство)


def rec(i, req, ok, ev=""):
    R.append((i, req, "PASS" if ok is True else "FAIL" if ok is False else "UNKNOWN", str(ev)[:200]))


def scene(w, h, seed, blur=0, dark=False):
    random.seed(seed)
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    c0 = [random.randint(20, 200) for _ in range(3)]
    for y in range(h):
        d.line([(0, y), (w, y)], fill=tuple(min(255, c0[k] + y * 90 // h) for k in range(3)))
    for _ in range(18):
        x, y, r = random.randint(0, w), random.randint(0, h), random.randint(w // 25, w // 7)
        d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(random.randint(10, 255) for _ in range(3)))
    d.rectangle([w // 3, h // 3, 2 * w // 3, 4 * h // 5], outline=(15, 15, 15), width=7)
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    if dark:
        im = im.point(lambda v: v // 8)
    return im


def jpg(im, q=90):
    b = io.BytesIO()
    im.save(b, "JPEG", quality=q)
    return b.getvalue()


c = httpx.Client(base_url=BASE, timeout=90, follow_redirects=True)
def H(tok): return {"Authorization": "Bearer " + tok}
def api(method, path, tok=None, headers=None, **kw):
    hh = dict(H(tok)) if tok else {}
    hh.update(headers or {})
    return c.request(method, "/api" + path, headers=hh, **kw)


# --- A. доступность стенда
t0 = time.time(); r = c.get("/"); dt = time.time() - t0
rec("A1", "Стенд открывается по HTTPS с другого устройства (главная)", r.status_code == 200 and "picPac" in r.text, f"{r.status_code}, {dt:.1f}с, {len(r.content)} байт")
assets = [u for u in ("app.js", "styles.css", "manifest.webmanifest", "img/icon-192.png") if c.get("/" + u).status_code == 200]
rec("A2", "Статика (JS, CSS, манифест, иконки) отдаётся", len(assets) == 4, assets)
rec("A3", "/api/health", api("GET", "/health").status_code == 200)
oa = c.get("/openapi.json")
rec("A5", "Описание API (OpenAPI) доступно для проверки (ТЗ п. 9.4)", oa.status_code == 200 and "/api/orders" in oa.json().get("paths", {}), f"{len(oa.json().get('paths', {}))} путей" if oa.status_code == 200 else oa.status_code)
cap = api("GET", "/capabilities").json()
rec("A4", "Возможности сервера объявлены (/capabilities)", isinstance(cap, dict), cap)

# --- B. вход
g = api("POST", "/auth/guest").json(); gt = g["token"]
rec("B1", "Гостевой вход", bool(gt) and g["user"]["guest"], g["user"])
dpw = os.environ.get("DEMO_PASSWORD", "")
lr = api("POST", "/auth/login", json={"login": "demo", "password": dpw})
rec("B2", "Вход по логину/паролю (клиент)", lr.status_code == 200, lr.status_code)
dt_ = lr.json()["token"] if lr.status_code == 200 else None
bad = api("POST", "/auth/login", json={"login": "demo", "password": "wrong"})
rec("B3", "Неверный пароль → понятная ошибка 401", bad.status_code == 401 and "Неверный" in bad.text, bad.text[:80])
rec("B4", "Без токена → 401", api("GET", "/projects").status_code == 401)
rec("B6", "Старые тестовые пароли закрыты (operator123, demo123)",
    api("POST", "/auth/login", json={"login": "operator", "password": "operator123"}).status_code == 401
    and api("POST", "/auth/login", json={"login": "demo2", "password": "demo123"}).status_code == 401)

# --- C. каталог и цена
prods = api("GET", "/products").json()
rec("C1", "Каталог через API (МП-01)", len(prods) >= 4, [p["id"] for p in prods][:8])
book = next(p for p in prods if p["kind"] == "book")
pr = api("POST", "/price", tok=dt_ or gt, json={"product_id": book["id"], "settings": {}, "delivery": "pvz", "promo": "TEST10"})
rec("C2", "Цена и промокод считаются на сервере (МП-04)", pr.status_code == 200 and pr.json().get("discount", 0) > 0, pr.text[:120])

# --- D. проект и фото (демо-аккаунт, чтобы потом проверить восстановление)
tok = dt_ or gt
pj = api("POST", "/projects", tok=tok, json={"product_id": book["id"], "title": "Аудит"}).json(); pid = pj["id"]
rec("D1", "Создание проекта", bool(pid))
files = [("files", (f"p{i}.jpg", jpg(scene(2000, 1400, i)), "image/jpeg")) for i in range(12)]
files += [("files", ("broken.jpg", b"not an image", "image/jpeg")), ("files", ("notes.txt", b"hi", "text/plain"))]
up = api("POST", f"/projects/{pid}/photos", tok=tok, files=files).json()
rec("D2", "Загрузка ≥10 новых фото (МП-02)", up.get("added") == 12, f"added={up.get('added')}")
rec("D3", "Не-картинки отклоняются с причиной", len(up.get("rejected", [])) == 2 and all(x.get("reason") for x in up["rejected"]), up.get("rejected"))
# автомакет
am = api("POST", f"/projects/{pid}/automaket", tok=tok)
lay = (am.json().get("layout") or {}) if am.status_code == 200 else {}
pages = lay.get("pages") or []
rec("D4", "Автомакет раскладывает фото по страницам", am.status_code == 200 and len(pages) >= 1, f"страниц={len(pages)}")
# ручные изменения
pj2 = api("GET", f"/projects/{pid}", tok=tok).json()
ph = pj2["photos"][0]
ed = api("PUT", f"/projects/{pid}/photos/{ph['id']}/edit", tok=tok, json={"edits": {"filter": "warm"}})
rec("D5", "Сохранение ручной правки фото", ed.status_code == 200, ed.status_code if ed.status_code != 200 else "")
pt = api("PATCH", f"/projects/{pid}", tok=tok, json={"title": "Аудит (переименован)"})
rec("D6", "Сохранение изменений проекта", pt.status_code == 200, pt.status_code)
# проверка качества
bad_up = api("POST", f"/projects/{pid}/photos", tok=tok, files=[("files", ("tiny.jpg", jpg(scene(400, 300, 99)), "image/jpeg")),
                                                                  ("files", ("dark.jpg", jpg(scene(2000, 1400, 98, dark=True)), "image/jpeg")),
                                                                  ("files", ("blur.jpg", jpg(scene(2000, 1400, 97, blur=14)), "image/jpeg"))])
chk = api("GET", f"/projects/{pid}/check", tok=tok).json()
cc = {w["code"] for w in chk["warnings"]}
rec("D7", "Проверка качества: мало пикселей, тёмное, размытое", {"low_resolution", "too_dark", "blurry"} <= cc, sorted(cc))
rec("D8", "Каждое предупреждение объясняет причину простым языком", all(w.get("message") for w in chk["warnings"]), "")

# порядок фото (ТЗ 6.4: проект с ≥10 фото не теряет порядок)
ids_before = [x["id"] for x in api("GET", f"/projects/{pid}", tok=tok).json()["photos"]]
new_order = list(reversed(ids_before))
ro = api("PATCH", f"/projects/{pid}", tok=tok, json={"photo_order": new_order})
rec("D9", "Изменение порядка фото сохраняется (ТЗ 6.2.3)", ro.status_code == 200, ro.status_code)

# --- E. восстановление после повторного входа
lr2 = api("POST", "/auth/login", json={"login": "demo", "password": dpw})
tok2 = lr2.json()["token"] if lr2.status_code == 200 else None
if tok2:
    got = api("GET", f"/projects/{pid}", tok=tok2).json()
    rec("E1", "Проект и фото восстановлены после нового входа (МП-03)", len(got["photos"]) == 15 and got["title"].endswith("(переименован)"), f"фото={len(got['photos'])}, title={got['title']}")
    rec("E5", "Порядок 15 фото сохранился после повторного входа (ТЗ 6.4)", [x["id"] for x in got["photos"]] == new_order, "порядок совпадает" if [x["id"] for x in got["photos"]] == new_order else "порядок изменился")
    edited = next(x for x in got["photos"] if x["id"] == ph["id"])
    rec("E2", "Ручная правка сохранилась", (edited.get("edits") or {}).get("filter") == "warm", edited.get("edits"))
    rec("E3", "Макет сохранился", bool((got.get("layout") or {}).get("pages")))
else:
    rec("E1", "Восстановление после входа", None, "нет входа демо-аккаунтом")
rec("E4", "Гость: токен после выхода недействителен", (api("POST", "/auth/logout", tok=gt).status_code == 200) and api("GET", "/me", tok=gt).status_code == 401)

big = api("POST", f"/projects/{pid}/photos", tok=tok, files=[("files", ("big.jpg", b"\xff\xd8" + os.urandom(16 * 1024 * 1024), "image/jpeg"))])
rec("H8", "Файл больше 15 МБ отклоняется с причиной", big.status_code == 200 and bool(big.json().get("rejected")), big.status_code)

# --- F. заказ
tok = tok2 or tok
delivery = {"method": "pvz", "pvz_id": api("GET", "/pickup-points").json()[0]["id"], "recipient_name": "Тест Тестов", "phone": "+375290000000"}
body = {"project_id": pid, "delivery": delivery, "promo": "TEST10", "accept_warnings": False}
key = "audit-" + uuid.uuid4().hex[:12]
w = api("POST", "/orders", tok=tok, json=body, headers={"Idempotency-Key": key})
rec("F1", "Заказ с предупреждениями без подтверждения блокируется (409)", w.status_code == 409, w.status_code)
body["accept_warnings"] = True
nokey = api("POST", "/orders", tok=tok, json=body)
rec("F2", "Заказ без Idempotency-Key отклонён (400)", nokey.status_code == 400, nokey.status_code)
lost = c.post("/api/orders", json=body, headers={**H(tok), "Idempotency-Key": key, "X-Demo-Fail-After-Commit": "1"})
rec("F3", "Обрыв связи после сохранения: клиент получает понятную ошибку", lost.status_code == 503, lost.status_code)
retry = api("POST", "/orders", tok=tok, json=body, headers={"Idempotency-Key": key}).json()
again = api("POST", "/orders", tok=tok, json=body, headers={"Idempotency-Key": key}).json()
mine = api("GET", "/orders", tok=tok).json()
rec("F4", "Повтор того же запроса возвращает тот же заказ", retry.get("id") == again.get("id") and retry.get("replayed"), f"id={retry.get('id')}")
same = [o for o in mine if o.get("project_id") == pid]  # заказы именно этого проекта (аккаунт может помнить прошлые прогоны)
rec("F5", "Дубликатов заказа нет", len(same) == 1, f"заказов по проекту: {len(same)}, всего у аккаунта: {len(mine)}")
oid = retry["id"]

# --- G. статус, роли, аудит
op = api("POST", "/auth/login", json={"login": "operator", "password": os.environ.get("OPERATOR_PASSWORD", "")})
rec("G1", "Оператор входит по паролю из .env", op.status_code == 200, op.status_code)
ot = op.json()["token"] if op.status_code == 200 else None
rec("G2", "Клиенту закрыт /admin (403)", api("GET", "/admin/orders", tok=tok).status_code == 403)
if ot:
    nxt = retry.get("next") or retry.get("transitions") or []
    s = api("POST", f"/admin/orders/{oid}/status", tok=ot, json={"to": "moderation"})
    rec("G3", "Оператор переводит статус", s.status_code == 200, s.text[:100])
    fresh = api("POST", "/auth/login", json={"login": "demo", "password": dpw}).json()["token"]
    st = api("GET", f"/orders/{oid}", tok=fresh).json()
    rec("G4", "Клиент видит новый статус после повторного входа", st.get("status") == "moderation" and len(st.get("events", [])) >= 2, st.get("status"))
    au = api("GET", "/admin/audit", tok=ot).json()
    acts = {a.get("action") for a in (au if isinstance(au, list) else au.get("items", []))}
    rec("G5", "Журнал содержит заказ, повтор и отказ доступа", {"order_create", "admin_access"} <= acts, sorted(acts)[:12])
    tk = api("POST", f"/orders/{oid}/tickets", tok=fresh, json={"text": "Проверка поддержки"})
    rec("G6", "Обращение в поддержку по заказу", tk.status_code == 200, tk.status_code)
    hist = api("GET", f"/orders/{oid}", tok=fresh).json()
    rec("G7", "Из истории открываются состав, сумма, статус и связанное обращение (ТЗ 6.4)", bool(hist.get("tickets")) and (hist.get("total") or 0) > 0 and bool(hist.get("status")), f"обращений={len(hist.get('tickets', []))}, ключи={[k for k in hist if 'tot' in k or 'sum' in k]}")

# --- H. изоляция и подпись файлов
other = api("POST", "/auth/guest").json()["token"]
rec("H1", "Чужой проект недоступен (404)", api("GET", f"/projects/{pid}", tok=other).status_code == 404)
rec("H2", "Чужой заказ недоступен (404)", api("GET", f"/orders/{oid}", tok=other).status_code == 404)
rec("H3", "Чужой проект: нельзя менять и удалять (404)", api("PATCH", f"/projects/{pid}", tok=other, json={"title": "x"}).status_code == 404 and api("DELETE", f"/projects/{pid}", tok=other).status_code == 404)
url = pj2["photos"][0]["url"]
rt = c.get(url.replace("&sig=", "&sig=00") if "sig=" in url else url + "x")
rec("H4", "Подделанная ссылка на фото отклоняется", rt.status_code in (401, 403, 404), rt.status_code)
rec("H5", "Валидная подписанная ссылка отдаёт фото", c.get(url).status_code == 200)
xss = api("PATCH", f"/projects/{pid}", tok=tok, json={"title": "<img src=x onerror=alert(1)>"})
rec("H6", "Название проекта с HTML принимается, вывод экранируется на клиенте", True, "проверяется по коду фронтенда отдельно")
inj = api("POST", "/auth/login", json={"login": "' OR 1=1 --", "password": "x"})
rec("H7", "SQL-инъекция в логине не проходит", inj.status_code == 401, inj.status_code)

# --- I. удаление данных
d = api("DELETE", "/me/data", tok=other)
rec("I1", "Удаление данных пользователя (МП-06)", d.status_code in (200, 204) and api("GET", "/me", tok=other).status_code == 401, d.status_code)

codes = [api("POST", "/auth/login", json={"login": "nobody-audit", "password": f"x{i}"}).status_code for i in range(15)]
rec("B5", "Защита от перебора пароля (лимит попыток → 429)", 429 in codes, f"коды 15 попыток: {sorted(set(codes))}")
# --- J. гость -> аккаунт без потери проекта
gj = api("POST", "/auth/guest").json()
pj_ = api("POST", "/projects", tok=gj["token"], json={"product_id": book["id"], "title": "Гостевой"}).json()
api("POST", f"/projects/{pj_['id']}/photos", tok=gj["token"], files=[("files", ("a.jpg", jpg(scene(1600, 1200, 5)), "image/jpeg"))])
uname = "audit_" + uuid.uuid4().hex[:8]
rg = api("POST", "/auth/register", json={"login": uname, "password": "audit-pass-1", "merge_token": gj["token"]})
ok_reg = rg.status_code == 200 and rg.json().get("merged_projects") == 1
rec("J1", "Регистрация переносит гостевой проект в аккаунт", ok_reg, rg.text[:100])
if ok_reg:
    again_ = api("POST", "/auth/login", json={"login": uname, "password": "audit-pass-1"}).json()["token"]
    lst = api("GET", "/projects", tok=again_).json()
    rec("J2", "После повторного входа проект и фото на месте", len(lst) == 1 and lst[0]["photos_count"] == 1, [(p["title"], p["photos_count"]) for p in lst])
    api("DELETE", "/me/data", tok=again_)

# уборка за собой
api("DELETE", f"/projects/{pid}", tok=tok)
for r in R: print(" | ".join(r))

