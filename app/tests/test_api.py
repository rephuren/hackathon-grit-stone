"""Сквозные тесты API. Запуск: .venv/bin/python -m pytest -q  (из папки app)"""
import hashlib
import hmac
import io
import json
import os
import re
import tempfile
import time
from urllib.parse import urlencode

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="picpac_test_")
os.environ["DEMO_MODE"] = "1"
os.environ["SEED_TEST_USERS"] = "1"  # тесты не зависят от локального app/.env
os.environ["OPERATOR_PASSWORD"] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["AI_GENERATE"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

from backend import security  # noqa: E402
from backend.main import app  # noqa: E402


def noise(w=2400, h=1800, scale=1.0):
    img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    if scale != 1.0:
        img = img.point(lambda v: int(v * scale))
    return img


def jpeg(img):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def files(*items):
    return [("files", (name, data, "image/jpeg")) for name, data in items]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def login(client, user, password):
    r = client.post("/api/auth/login", json={"login": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


@pytest.fixture(scope="module")
def anna(client):
    return login(client, "demo", "demo123")


@pytest.fixture(scope="module")
def igor(client):
    return login(client, "demo2", "demo123")


@pytest.fixture(scope="module")
def oper(client):
    return login(client, "operator", "operator123")


def new_project(client, h, product="premium"):
    r = client.post("/api/projects", json={"product_id": product, "title": "Тест"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_login_and_roles(client, anna, oper):
    bad = client.post("/api/auth/login", json={"login": "demo", "password": "x"})
    assert bad.status_code == 401
    assert client.get("/api/me", headers=anna).json()["role"] == "client"
    assert client.get("/api/admin/orders", headers=anna).status_code == 403  # серверная проверка роли
    assert client.get("/api/admin/orders").status_code == 401
    audit = client.get("/api/admin/audit", headers=oper).json()
    assert any(a["action"] == "admin_access" and a["result"] == "denied" for a in audit)


def test_guest(client):
    r = client.post("/api/auth/guest")
    assert r.status_code == 200 and r.json()["user"]["guest"] is True


def test_price_rules(client, anna):
    def price(**kw):
        body = {"product_id": "classic", "settings": {"size": "20x20", "pages": 20}, "delivery": "pvz", **kw}
        r = client.post("/api/price", json=body, headers=anna)
        assert r.status_code == 200, r.text
        return r.json()

    assert price()["subtotal"] == 8100
    assert price(settings={"size": "20x20", "pages": 24})["subtotal"] == 8700  # +3 руб. за каждые 2 стр.
    assert price(promo="TEST10")["discount"] == 810
    assert price(promo="FREESHIP")["delivery_cost"] == 0
    assert price(delivery="belpost", settings={"size": "21x30", "pages": 60})["delivery_cost"] == 0  # от 150 руб.
    assert price(promo="NOPE")["promo_error"]
    bad = client.post("/api/price", json={"product_id": "classic", "settings": {"pages": 21}}, headers=anna)
    assert bad.status_code == 422


def test_preflight_and_full_order_flow(client, anna):
    pid = new_project(client, anna)
    good = [(f"ok{i}.jpg", jpeg(noise())) for i in range(7)]
    r = client.post(f"/api/projects/{pid}/photos", files=files(*good), headers=anna)
    assert r.json()["added"] == 7

    # проблемные файлы
    sharp = noise()
    extra = [("tiny.jpg", jpeg(noise(500, 400))),
             ("blur.jpg", jpeg(noise(2400, 1800).filter(ImageFilter.GaussianBlur(14)))),
             ("dark.jpg", jpeg(noise(scale=0.12))),
             ("dup1.jpg", jpeg(sharp)), ("dup2.jpg", jpeg(sharp)),
             ("broken.jpg", b"not an image"), ("notes.txt", b"hello")]
    r = client.post(f"/api/projects/{pid}/photos", files=files(*extra), headers=anna).json()
    assert r["added"] == 5 and len(r["rejected"]) == 2

    chk = client.get(f"/api/projects/{pid}/check", headers=anna).json()
    codes = {w["code"] for w in chk["warnings"]}
    assert {"low_resolution", "blurry", "too_dark", "duplicate"} <= codes, codes
    assert any(b["code"] == "no_layout" for b in chk["blocking"])  # макета ещё нет

    lay = client.post(f"/api/projects/{pid}/automaket", headers=anna).json()["layout"]
    assert lay["unplaced"] == 0 and len(lay["pages"]) == 10
    chk = client.get(f"/api/projects/{pid}/check", headers=anna).json()
    assert chk["ok"] is True
    # тематическое оформление страниц: применяется и переживает пересборку макета
    lay = client.post(f"/api/projects/{pid}/automaket?theme=travel", headers=anna).json()["layout"]
    assert lay["theme"] == "travel" and all("variant" in pg for pg in lay["pages"])
    lay = client.post(f"/api/projects/{pid}/automaket?sort=manual", headers=anna).json()["layout"]
    assert lay["theme"] == "travel"
    assert client.post(f"/api/projects/{pid}/automaket?theme=evil", headers=anna).status_code == 422
    client.post(f"/api/projects/{pid}/automaket?theme=classic", headers=anna)

    # «Улучшить» поднимает яркость тёмного кадра
    proj = client.get(f"/api/projects/{pid}", headers=anna).json()
    dark_id = next(p["id"] for p in proj["photos"] if p["name"] == "dark.jpg")
    client.post(f"/api/projects/{pid}/photos/{dark_id}/enhance", headers=anna)
    codes_after = {w["code"] for w in client.get(f"/api/projects/{pid}/check", headers=anna).json()["warnings"]
                   if w["photo_id"] == dark_id}
    assert "too_dark" not in codes_after

    delivery = {"method": "pvz", "pvz_id": "pvz-01", "recipient_name": "Анна", "phone": "+375291234567"}
    body = {"project_id": pid, "delivery": delivery, "promo": "TEST10"}
    assert client.post("/api/orders", json=body, headers=anna).status_code == 400  # нет Idempotency-Key
    key = {**anna, "Idempotency-Key": "test-key-0001"}
    r = client.post("/api/orders", json=body, headers=key)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "warnings_need_ack"
    body["accept_warnings"] = True
    first = client.post("/api/orders", json=body, headers=key)
    assert first.status_code == 200, first.text
    again = client.post("/api/orders", json=body, headers=key)  # безопасный повтор
    assert again.json()["id"] == first.json()["id"] and again.json()["replayed"] is True
    assert len(client.get("/api/orders", headers=anna).json()) == 1
    # проект заблокирован после заказа
    assert client.post(f"/api/projects/{pid}/photos", files=files(("x.jpg", jpeg(noise()))), headers=anna).status_code == 409


def scene(w=2400, h=1800, obj_x=0.5, bright=120):
    """Спокойный фон и одна контрастная деталь («объект») на заданной горизонтали."""
    img = Image.new("RGB", (w, h), (bright, bright, bright))
    px = img.load()
    cx, r = int(obj_x * w), int(h * 0.12)
    for y in range(int(h * 0.4), int(h * 0.4) + 2 * r):
        for x in range(max(0, cx - r), min(w, cx + r)):
            px[x, y] = (255, 40, 40) if (x // 20 + y // 20) % 2 else (20, 20, 20)
    return img.filter(ImageFilter.GaussianBlur(1))


def test_quality_checks_cleanup_and_formats(client, anna):
    pid = new_project(client, anna, "print")
    sharp = scene()
    soft = sharp.filter(ImageFilter.GaussianBlur(12))
    black = Image.new("RGB", (2400, 1800), (3, 3, 3))
    white = Image.new("RGB", (2400, 1800), (252, 252, 252))
    edge = scene(w=3000, h=1000, obj_x=0.97)  # панорама: объект у самого края
    r = client.post(f"/api/projects/{pid}/photos", files=files(
        ("a.jpg", jpeg(sharp)), ("a_copy.jpg", jpeg(soft)), ("black.jpg", jpeg(black)),
        ("white.jpg", jpeg(white)), ("edge.jpg", jpeg(edge))), headers=anna)
    assert r.status_code == 200 and r.json()["added"] == 5, r.text
    chk = client.get(f"/api/projects/{pid}/check", headers=anna).json()
    by = {}
    for w in chk["warnings"] + chk["tips"]:
        by.setdefault(w["code"], []).append(w["photo_id"])
    names = {p["id"]: p["name"] for p in r.json()["project"]["photos"]}
    assert "too_dark" in by and "overexposed" in by
    assert {names[i] for i in by["too_dark"]} == {"black.jpg"}
    assert {names[i] for i in by["overexposed"]} == {"white.jpg"}
    assert any(names[i] == "edge.jpg" for i in by.get("crop_risk", []))
    assert {names[i] for i in by["extreme_aspect"]} == {"edge.jpg"} if "extreme_aspect" in by else True
    # резкий кадр и его смазанная копия: дубликат — это смазанный, лучший остаётся
    assert len(chk["duplicates"]) == 1
    keep = chk["duplicates"][0]["keep"]
    assert names[keep] == "a.jpg" and [names[i] for i in chk["duplicates"][0]["remove"]] == ["a_copy.jpg"]
    assert {names[b["photo_id"]] for b in chk["bad"]} >= {"black.jpg", "white.jpg"}
    out = client.post(f"/api/projects/{pid}/cleanup", json={"duplicates": True, "bad": True}, headers=anna).json()
    left = {p["name"] for p in out["project"]["photos"]}
    assert "a.jpg" in left and "a_copy.jpg" not in left and "black.jpg" not in left and "white.jpg" not in left
    assert out["duplicates"] >= 1 and out["bad"] >= 2

    # форматы: GIF отклоняется с понятной причиной, WEBP и прозрачный PNG принимаются с подсказкой
    gif = io.BytesIO(); Image.new("RGB", (400, 400), "red").save(gif, "GIF")
    webp = io.BytesIO(); scene().save(webp, "WEBP")
    png = io.BytesIO(); Image.new("RGBA", (2400, 1800), (200, 30, 30, 0)).save(png, "PNG")
    r = client.post(f"/api/projects/{pid}/photos", files=[
        ("files", ("x.gif", gif.getvalue(), "image/gif")), ("files", ("y.webp", webp.getvalue(), "image/webp")),
        ("files", ("z.png", png.getvalue(), "image/png"))], headers=anna).json()
    assert r["added"] == 2 and "не подходит для печати" in r["rejected"][0]["reason"]
    codes = {t["code"] for t in client.get(f"/api/projects/{pid}/check", headers=anna).json()["tips"]}
    assert {"format_converted", "transparent"} <= codes


def test_network_loss_after_commit_creates_no_duplicate(client, igor):
    pid = new_project(client, igor)
    client.post(f"/api/projects/{pid}/photos", files=files(*[(f"p{i}.jpg", jpeg(noise())) for i in range(10)]), headers=igor)
    client.post(f"/api/projects/{pid}/automaket", headers=igor)
    body = {"project_id": pid, "delivery": {"method": "belpost", "address": "Минск, Тестовая 1",
                                            "recipient_name": "Игорь", "phone": "375291111111"}}
    h = {**igor, "Idempotency-Key": "lost-response-01"}
    lost = client.post("/api/orders", json=body, headers={**h, "X-Demo-Fail-After-Commit": "1"})
    assert lost.status_code == 503
    retry = client.post("/api/orders", json=body, headers=h)
    assert retry.status_code == 200 and retry.json()["replayed"] is True
    assert len(client.get("/api/orders", headers=igor).json()) == 1


def test_access_control_between_users(client, anna, igor):
    pid = new_project(client, anna, "print")
    assert client.get(f"/api/projects/{pid}", headers=igor).status_code == 404
    assert client.post(f"/api/projects/{pid}/photos", files=files(("a.jpg", jpeg(noise()))), headers=igor).status_code == 404
    proj = client.post(f"/api/projects/{pid}/photos", files=files(("a.jpg", jpeg(noise()))), headers=anna).json()["project"]
    url = proj["photos"][0]["thumb"]
    assert client.get(url).status_code == 200
    assert client.get(re.sub(r"sig=[0-9a-f]{3}", "sig=abc", url)).status_code == 403  # подпись подделана
    orders = client.get("/api/orders", headers=anna).json()
    assert client.get(f"/api/orders/{orders[0]['id']}", headers=igor).status_code == 404


def test_operator_status_flow_and_tickets(client, anna, oper):
    order = client.get("/api/orders", headers=anna).json()[0]
    oid = order["id"]
    skip = client.post(f"/api/admin/orders/{oid}/status", json={"to": "ready"}, headers=oper)
    assert skip.status_code == 409  # нельзя перепрыгнуть этап
    assert order["tracking"] is None  # трек-номера до передачи в доставку нет
    for to in ["moderation", "production", "ready", "completed"]:
        r = client.post(f"/api/admin/orders/{oid}/status", json={"to": to}, headers=oper)
        assert r.status_code == 200, r.text
        if to == "ready":  # как в лекции: после отправки клиент видит трек-номер в кабинете
            assert re.fullmatch(r"RR\d{9}BY", r.json()["tracking"]["number"]) and r.json()["tracking"]["demo"]
    got = client.get(f"/api/orders/{oid}", headers=anna).json()
    assert got["tracking"]["number"] == r.json()["tracking"]["number"]  # номер стабилен
    assert got["status"] == "completed" and [e["status"] for e in got["events"]][-1] == "completed"
    t = client.post(f"/api/orders/{oid}/tickets", json={"text": "Где мой заказ?"}, headers=anna).json()
    assert t["tickets"][0]["text"] == "Где мой заказ?"
    assert client.post(f"/api/admin/orders/{oid}/status", json={"to": "moderation"}, headers=anna).status_code == 403
    assert len(client.get("/api/admin/funnel", headers=oper).json()) == 7


def test_telegram_signature():
    token = "123456:TEST-TOKEN"
    user = json.dumps({"id": 42, "first_name": "Тест"}, ensure_ascii=False)
    fields = {"auth_date": str(int(time.time())), "query_id": "AAA", "user": user}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    sig = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    good = urlencode({**fields, "hash": sig})
    assert security.verify_telegram_init_data(good, token)["id"] == 42
    assert security.verify_telegram_init_data(good, "other-token") is None
    forged = urlencode({**fields, "user": json.dumps({"id": 1}), "hash": sig})
    assert security.verify_telegram_init_data(forged, token) is None


def test_editor_is_non_destructive(client, anna, igor):
    pid = new_project(client, anna, "print")
    proj = client.post(f"/api/projects/{pid}/photos", files=files(("e.jpg", jpeg(noise(2000, 1500)))), headers=anna).json()["project"]
    ph = proj["photos"][0]
    assert (ph["width"], ph["height"]) == (2000, 1500)
    # предпросмотр ничего не сохраняет
    edits = {"crop": {"ratio": "1:1", "zoom": 2}, "filter": "bw", "adjust": {"brightness": 20},
             "text": {"value": "Наше лето", "pos": "bottom"}, "rotate": 90, "evil": "<script>"}
    pv = client.post(f"/api/projects/{pid}/photos/{ph['id']}/preview", json={"edits": edits, "size": 300}, headers=anna)
    assert pv.status_code == 200 and pv.headers["content-type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(pv.content)).size) <= 300
    assert client.get(f"/api/projects/{pid}", headers=anna).json()["photos"][0]["edits"] == {}
    # чужой пользователь не может ни смотреть, ни править
    assert client.post(f"/api/projects/{pid}/photos/{ph['id']}/preview", json={"edits": edits}, headers=igor).status_code == 404
    assert client.put(f"/api/projects/{pid}/photos/{ph['id']}/edit", json={"edits": edits}, headers=igor).status_code == 404
    # сохранение правок: версия меняется, оригинал остаётся
    saved = client.put(f"/api/projects/{pid}/photos/{ph['id']}/edit", json={"edits": edits}, headers=anna).json()["photos"][0]
    assert saved["edits"]["filter"] == "bw" and "evil" not in saved["edits"]
    assert saved["width"] == saved["height"] == 750  # поворот → 1500×2000, кадр 1:1 = 1500, зум ×2 = 750
    orig = Image.open(io.BytesIO(client.get(saved["orig"]).content))
    assert orig.size == (2000, 1500)
    # кадрирование уменьшает пиксели, и это сразу видно в предпечатной проверке (для 10×15 нужно 886 px)
    codes = {w["code"] for w in client.get(f"/api/projects/{pid}/check", headers=anna).json()["warnings"]}
    assert "low_resolution" in codes
    # возврат к оригиналу
    back = client.put(f"/api/projects/{pid}/photos/{ph['id']}/edit", json={"edits": {}}, headers=anna).json()["photos"][0]
    assert back["edits"] == {} and (back["width"], back["height"]) == (2000, 1500)
    # автоулучшение тоже обратимая правка
    enh = client.post(f"/api/projects/{pid}/photos/{ph['id']}/enhance", headers=anna).json()["photos"][0]
    assert enh["edits"] == {"auto": True}


def test_ai_generate_and_accept(client, anna, igor, monkeypatch):
    import backend.main as m

    monkeypatch.setattr(m, "fetch_generated", lambda prompt: jpeg(noise(1024, 1024)))
    m._last_generate.clear()
    pid = new_project(client, anna, "print")
    r = client.post(f"/api/projects/{pid}/ai/generate", json={"prompt": "горное озеро на рассвете", "style": "cinema"}, headers=anna)
    assert r.status_code == 200, r.text
    assert client.get(r.json()["url"]).status_code == 200
    again = client.post(f"/api/projects/{pid}/ai/generate", json={"prompt": "ещё раз", "style": "photo"}, headers=anna)
    assert again.status_code == 429  # защита от частых запросов
    other = client.post(f"/api/projects/{pid}/ai/accept", json={"tmp_id": r.json()["tmp_id"]}, headers=igor)
    assert other.status_code == 404  # чужой кадр принять нельзя
    ok = client.post(f"/api/projects/{pid}/ai/accept", json={"tmp_id": r.json()["tmp_id"]}, headers=anna)
    assert ok.status_code == 200 and len(ok.json()["photos"]) == 1
    assert client.post(f"/api/projects/{pid}/ai/accept", json={"tmp_id": "../../etc/passwd"}, headers=anna).status_code == 422

    def boom(prompt):
        raise RuntimeError("provider down")

    monkeypatch.setattr(m, "fetch_generated", boom)
    m._last_generate.clear()
    down = client.post(f"/api/projects/{pid}/ai/generate", json={"prompt": "сбой провайдера"}, headers=anna)
    assert down.status_code == 502 and down.json()["detail"]["code"] == "provider_error"
    caps = client.get("/api/capabilities").json()
    assert "bg_removal" in caps and caps["ai_generate"] is True


def test_reorder_clones_project(client, anna, igor):
    """«Заказать ещё»: копия проекта с фото и правками, исходный заказ не трогаем, чужой проект недоступен."""
    pid = new_project(client, anna, "print")
    client.post(f"/api/projects/{pid}/photos", files=files(("a.jpg", jpeg(noise())), ("b.jpg", jpeg(noise()))), headers=anna)
    photo = client.get(f"/api/projects/{pid}", headers=anna).json()["photos"][0]
    client.put(f"/api/projects/{pid}/photos/{photo['id']}/edit", json={"edits": {"filter": "bw"}}, headers=anna)
    assert client.post(f"/api/projects/{pid}/clone", headers=igor).status_code == 404
    r = client.post(f"/api/projects/{pid}/clone", headers=anna)
    assert r.status_code == 200, r.text
    copy = r.json()
    assert copy["id"] != pid and copy["status"] == "draft" and len(copy["photos"]) == 2
    assert copy["photos"][0]["id"] != photo["id"] and copy["photos"][0]["edits"] == {"filter": "bw"}
    assert client.get(copy["photos"][0]["url"]).status_code == 200  # файлы скопированы
    orders = client.get("/api/orders", headers=anna).json()
    assert all("project_id" in o and "cover" in o for o in orders)


def test_telegram_login_and_order_notifications(client, oper, monkeypatch):
    """Вход через Telegram по подписи и уведомления о заказе только пользователям Telegram."""
    from backend import tgbot
    token = "777:TEST"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    sent = []
    monkeypatch.setattr(tgbot, "send", lambda chat, text, btn=False: sent.append((chat, text)))
    monkeypatch.setattr(tgbot.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    user = json.dumps({"id": 9001, "first_name": "Миша"}, ensure_ascii=False)
    fields = {"auth_date": str(int(time.time())), "query_id": "Q", "user": user}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    sig = hmac.new(hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest(), check.encode(), hashlib.sha256).hexdigest()
    r = client.post("/api/auth/telegram", json={"init_data": urlencode({**fields, "hash": sig})})
    assert r.status_code == 200 and r.json()["user"]["tg"] is True
    bad = client.post("/api/auth/telegram", json={"init_data": urlencode({**fields, "hash": "0" * 64})})
    assert bad.status_code == 401
    h = {"Authorization": "Bearer " + r.json()["token"]}
    pid = new_project(client, h, "print")
    client.post(f"/api/projects/{pid}/photos", files=files(("a.jpg", jpeg(noise()))), headers=h)
    order = client.post("/api/orders", json={"project_id": pid, "accept_warnings": True, "delivery": {
        "method": "pvz", "pvz_id": "pvz-01", "recipient_name": "Миша", "phone": "+375291234567"}},
        headers={**h, "Idempotency-Key": "tg-key-0001"}).json()
    assert sent and sent[0][0] == "9001" and order["number"] in sent[0][1]
    client.post(f"/api/admin/orders/{order['id']}/status", json={"to": "moderation"}, headers=oper)
    assert len(sent) == 2 and "9001" == sent[1][0]
    # обычный пользователь без Telegram уведомлений не получает
    guest = client.post("/api/auth/guest").json()
    before = len(sent)
    tgbot.notify_user(guest["user"]["id"], "тест")
    assert len(sent) == before


@pytest.mark.skipif(not os.path.exists(os.path.join(os.path.dirname(__file__), "..", "data", "models", "realesr-general-x4v3.onnx")),
                    reason="нет модели улучшения качества (scripts/fetch_models.py)")
def test_upscale_is_reversible(client, anna, monkeypatch):
    monkeypatch.setenv("UPSCALE_MODEL", os.path.join(os.path.dirname(__file__), "..", "data", "models", "realesr-general-x4v3.onnx"))
    assert client.get("/api/capabilities").json()["upscale"] is True
    pid = new_project(client, anna, "print")
    client.post(f"/api/projects/{pid}/photos", files=files(("s.jpg", jpeg(noise(480, 360)))), headers=anna)
    ph = client.get(f"/api/projects/{pid}", headers=anna).json()["photos"][0]
    assert (ph["width"], ph["height"]) == (480, 360) and ph["upscaled"] is False
    r = client.post(f"/api/projects/{pid}/photos/{ph['id']}/upscale", json={"scale": 4}, headers=anna)
    assert r.status_code == 200, r.text
    up = r.json()["photos"][0]
    assert (up["width"], up["height"]) == (1920, 1440) and up["upscaled"] is True
    cmp = client.get(f"/api/projects/{pid}/photos/{ph['id']}/upscale-compare", headers=anna)
    assert cmp.status_code == 200 and cmp.json()["before"].startswith("data:image/jpeg") and cmp.json()["scale"] == 4.0
    # после нейросетевого улучшения проверка размытия по миниатюре не ругается, а даёт совет
    chk = client.get(f"/api/projects/{pid}/check", headers=anna).json()
    assert "blurry" not in {w["code"] for w in chk["warnings"]} and "enhanced" in {t["code"] for t in chk["tips"]}
    # правки поверх увеличенной версии не теряют размер, а возврат восстанавливает оригинал
    client.put(f"/api/projects/{pid}/photos/{ph['id']}/edit", json={"edits": {"filter": "bw"}}, headers=anna)
    assert client.get(f"/api/projects/{pid}", headers=anna).json()["photos"][0]["width"] == 1920
    back = client.delete(f"/api/projects/{pid}/photos/{ph['id']}/upscale", headers=anna).json()["photos"][0]
    assert (back["width"], back["height"]) == (480, 360) and back["upscaled"] is False and back["edits"] == {"filter": "bw"}


def test_object_removal_is_reversible(client, anna, monkeypatch):
    model = os.path.join(os.path.dirname(__file__), "..", "data", "models", "lama_fp32.onnx")
    if not os.path.exists(model):
        pytest.skip("нет модели LaMa (scripts/fetch_models.py inpaint)")
    import base64
    monkeypatch.setenv("INPAINT_MODEL", model)
    assert client.get("/api/capabilities").json()["object_removal"] is True
    pid = new_project(client, anna, "print")
    base = Image.new("RGB", (800, 600), (90, 150, 200))
    for x in range(300, 420):
        for y in range(200, 360):
            base.putpixel((x, y), (220, 40, 40))  # «лишний объект»
    client.post(f"/api/projects/{pid}/photos", files=files(("o.jpg", jpeg(base))), headers=anna)
    ph = client.get(f"/api/projects/{pid}", headers=anna).json()["photos"][0]
    mask = Image.new("L", (800, 600), 0)
    for x in range(290, 430):
        for y in range(190, 370):
            mask.putpixel((x, y), 255)
    buf = io.BytesIO(); mask.save(buf, "PNG")
    data = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    bad = client.post(f"/api/projects/{pid}/photos/{ph['id']}/erase", json={"mask": "data:image/png;base64,AAAA"}, headers=anna)
    assert bad.status_code == 422
    r = client.post(f"/api/projects/{pid}/photos/{ph['id']}/erase", json={"mask": data}, headers=anna)
    assert r.status_code == 200, r.text
    assert r.json()["photos"][0]["retouched"] is True
    # красное пятно исчезло: в центре удалённой области цвет близок к фону, а не к красному
    out = client.get(r.json()["photos"][0]["url"]).content
    px = Image.open(io.BytesIO(out)).convert("RGB").getpixel((360, 280))
    assert px[0] < 160 and px[2] > 120, px
    # после ретуши увеличение отклоняется, а возврат восстанавливает исходное
    back = client.delete(f"/api/projects/{pid}/photos/{ph['id']}/erase", headers=anna).json()["photos"][0]
    assert back["retouched"] is False
    px0 = Image.open(io.BytesIO(client.get(back["url"]).content)).convert("RGB").getpixel((360, 280))
    assert px0[0] > 180


def test_delete_my_data(client):
    h = login(client, "demo2", "demo123")
    r = client.delete("/api/me/data", headers=h)
    assert r.status_code == 200
    assert client.get("/api/me", headers=h).status_code == 401


def test_login_is_rate_limited_and_does_not_block_others(client):
    h = {"X-Forwarded-For": "203.0.113.7"}
    codes = [client.post("/api/auth/login", json={"login": "victim", "password": f"bad{i}"}, headers=h).status_code for i in range(10)]
    assert codes[:8] == [401] * 8 and codes[8:] == [429, 429], codes
    body = client.post("/api/auth/login", json={"login": "victim", "password": "x"}, headers=h).json()
    assert "Подождите" in body["detail"]["message"]
    # другой адрес и другой логин не заблокированы
    ok = client.post("/api/auth/login", json={"login": "demo", "password": "demo123"}, headers={"X-Forwarded-For": "203.0.113.8"})
    assert ok.status_code == 200
    # верный вход сбрасывает счётчик конкретной пары логин+адрес
    h2 = {"X-Forwarded-For": "203.0.113.9"}
    for i in range(5):
        client.post("/api/auth/login", json={"login": "demo", "password": f"bad{i}"}, headers=h2)
    assert client.post("/api/auth/login", json={"login": "demo", "password": "demo123"}, headers=h2).status_code == 200


def test_guest_projects_survive_register_and_login(client):
    g = client.post("/api/auth/guest").json()
    gh = {"Authorization": "Bearer " + g["token"]}
    pid = new_project(client, gh)
    client.post(f"/api/projects/{pid}/photos", files=files(("a.jpg", jpeg(noise(1200, 900)))), headers=gh)

    r = client.post("/api/auth/register", json={"login": "Newbie_1", "password": "secret1", "merge_token": g["token"]})
    assert r.status_code == 200, r.text
    assert r.json()["merged_projects"] == 1 and not r.json()["user"]["guest"]
    nh = {"Authorization": "Bearer " + r.json()["token"]}
    assert [p["id"] for p in client.get("/api/projects", headers=nh).json()] == [pid]
    assert len(client.get(f"/api/projects/{pid}", headers=nh).json()["photos"]) == 1
    assert client.get("/api/me", headers=gh).status_code == 401  # гостевая сессия закрыта вместе с гостем

    # повторный вход под тем же логином (регистр не важен) видит проект, логин занят для новой регистрации
    again = client.post("/api/auth/login", json={"login": "NEWBIE_1", "password": "secret1"})
    assert again.status_code == 200
    ah = {"Authorization": "Bearer " + again.json()["token"]}
    assert [p["id"] for p in client.get("/api/projects", headers=ah).json()] == [pid]
    dup = client.post("/api/auth/register", json={"login": "newbie_1", "password": "another1"})
    assert dup.status_code == 409 and "занят" in dup.json()["detail"]["message"]
    # слабые данные отклоняются
    assert client.post("/api/auth/register", json={"login": "ab", "password": "secret1"}).status_code == 422
    assert client.post("/api/auth/register", json={"login": "okname", "password": "123"}).status_code == 422


def test_merge_does_not_touch_real_accounts(client):
    # чужой «токен для переноса» от настоящего аккаунта не переносит ничего
    real = client.post("/api/auth/register", json={"login": "realuser_1", "password": "secret1"}).json()
    igor = {"Authorization": "Bearer " + real["token"]}
    token_of_igor = real["token"]
    pid = new_project(client, igor)
    r = client.post("/api/auth/register", json={"login": "thief_1", "password": "secret1", "merge_token": token_of_igor})
    assert r.status_code == 200 and r.json()["merged_projects"] == 0
    assert pid in [p["id"] for p in client.get("/api/projects", headers=igor).json()]


def test_heic_from_iphone_is_accepted_and_converted(client):
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    g = client.post("/api/auth/guest").json()
    h = {"Authorization": "Bearer " + g["token"]}
    pid = new_project(client, h, product="print")
    buf = io.BytesIO()
    noise(1600, 1200).save(buf, "HEIF", quality=80)
    r = client.post(f"/api/projects/{pid}/photos", files=[("files", ("IMG_0001.HEIC", buf.getvalue(), "image/heic"))], headers=h).json()
    assert r["added"] == 1 and not r["rejected"], r
    url = client.get(f"/api/projects/{pid}", headers=h).json()["photos"][0]["url"]
    img = client.get(url)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"


def test_ai_frames_survive_closing_the_app(client, monkeypatch):
    """Созданный кадр хранится на сервере: после выхода и повторного входа он в «Недавних», пока его не добавят."""
    import backend.main as m

    seen = []
    monkeypatch.setattr(m.G, "available", lambda: False)  # в тестах проверяем путь без локальной модели
    monkeypatch.setattr(m, "fetch_generated", lambda prompt: seen.append(prompt) or jpeg(noise(1024, 1024)))
    g = client.post("/api/auth/guest").json()
    h = {"Authorization": "Bearer " + g["token"]}
    pid = new_project(client, h, "print")
    made = []
    for text in ("мальчик на качелях в деревне", "море и мороженое"):
        m._last_generate.clear()
        r = client.post(f"/api/projects/{pid}/ai/generate", json={"prompt": text, "style": "photo"}, headers=h)
        assert r.status_code == 200, r.text
        made.append(r.json()["tmp_id"])
    rec = client.get(f"/api/projects/{pid}/ai/recent", headers=h).json()
    assert [x["tmp_id"] for x in rec] == made[::-1] and rec[0]["prompt"] == "море и мороженое"
    # другой пользователь чужие кадры не видит и удалить не может
    other = {"Authorization": "Bearer " + client.post("/api/auth/guest").json()["token"]}
    opid = new_project(client, other, "print")
    assert client.get(f"/api/projects/{opid}/ai/recent", headers=other).json() == []
    assert client.delete(f"/api/projects/{opid}/ai/recent/{made[0]}", headers=other).status_code == 404
    # добавленный в альбом кадр уходит из «Недавних», удалённый тоже
    client.post(f"/api/projects/{pid}/ai/accept", json={"tmp_id": made[1]}, headers=h)
    assert [x["tmp_id"] for x in client.get(f"/api/projects/{pid}/ai/recent", headers=h).json()] == [made[0]]
    assert client.delete(f"/api/projects/{pid}/ai/recent/{made[0]}", headers=h).json() == []
    assert all(p.endswith("photo") or "photo" in p for p in seen)  # к запросу добавлен стиль


def test_prompt_cleanup_for_image_model():
    from backend import imagegen

    assert imagegen.clean_prompt("Draw me a boy on swings in the village.") == "a boy on swings in the village"
    assert imagegen.clean_prompt("I want me standing on the seashore, not in anime style") .startswith("me standing on the seashore")
    full, en, _ = imagegen.build_prompt("a cat in a hat", "photo")
    ru, neg = imagegen.ru_clean("Я ХОЧУ ЧТОБЫ Я СТОЯЛА НА БЕРЕГУ МОРЯ И В РУКАХ ДЕРЖАЛА МОРОЖКО , сделай не в аниме стиле")
    assert ru == "Молодая женщина стояла на берегу моря и в руках держала мороженое" and "anime" in neg
    assert imagegen.ru_clean("Нарисуй мне мальчика на качелях в деревне")[0] == "Мальчика на качелях в деревне"
    assert full.startswith("a cat in a hat, realistic photo")


def test_projects_list_links_ordered_project_to_its_order(client, anna):
    lst = client.get("/api/projects", headers=anna).json()
    ordered = [p for p in lst if p["status"] != "draft"]
    assert ordered and all(p["order_id"] for p in ordered)
    assert all(p["order_id"] is None for p in lst if p["status"] == "draft")


def test_image_generation_queues_parallel_requests(monkeypatch):
    """Пять одновременных запросов не получают отказ: встают в очередь и выполняются по одному."""
    import threading
    import time as _t

    from PIL import Image as _I

    from backend import imagegen

    active, peak, done = [0], [0], []

    class FakeRes:
        nsfw_content_detected = [False]

        def __init__(self):
            self.images = [_I.new("RGB", (64, 64))]

    def fake_pipe(**kw):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        _t.sleep(0.15)
        active[0] -= 1
        return FakeRes()

    monkeypatch.setattr(imagegen, "_load", lambda: fake_pipe)
    monkeypatch.setattr(imagegen, "translate", lambda t: t)
    monkeypatch.setattr(imagegen, "QUEUE_WAIT", 5)
    ths = [threading.Thread(target=lambda: done.append(imagegen.generate("a cat", "photo", seed=1))) for _ in range(5)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert len(done) == 5 and peak[0] == 1  # все пятеро получили кадр, одновременно работала одна генерация


def test_smart_story_pin_exclude_return(client, anna):
    """Закрепить кадр, убрать фото из книги и вернуть его: файлы не теряются, макет и проверка учитывают выбор."""
    pid = new_project(client, anna)
    imgs = [(f"s{i}.jpg", jpeg(noise(2400, 1800, 0.5 + i / 20))) for i in range(12)]
    r = client.post(f"/api/projects/{pid}/photos", files=files(*imgs), headers=anna).json()
    assert r["added"] == 12
    lay = client.post(f"/api/projects/{pid}/automaket", headers=anna).json()
    ids = [p["id"] for p in lay["photos"]]
    assert lay["layout"]["unplaced"] == 0

    # закрепить: кадр получает отдельную страницу, все остальные по-прежнему размещены
    star = ids[3]
    p = client.patch(f"/api/projects/{pid}/photos/{star}/flags", json={"pinned": True}, headers=anna).json()
    assert [x for x in p["photos"] if x["id"] == star][0]["pinned"] is True
    pages = p["layout"]["pages"]
    assert [pg["photo_ids"] for pg in pages if star in pg["photo_ids"]] == [[star]]
    assert p["layout"]["unplaced"] == 0
    placed = [i for pg in pages for i in pg["photo_ids"]]
    assert sorted(placed) == sorted(ids)

    # убрать из книги: фото остаётся в проекте, но не в макете и не в счёте для проверки
    gone = ids[5]
    p = client.patch(f"/api/projects/{pid}/photos/{gone}/flags", json={"excluded": True}, headers=anna).json()
    assert len(p["photos"]) == 12 and [x for x in p["photos"] if x["id"] == gone][0]["excluded"] is True
    assert gone not in [i for pg in p["layout"]["pages"] for i in pg["photo_ids"]]
    chk = client.get(f"/api/projects/{pid}/check", headers=anna).json()
    assert chk["photos"] == 11

    # закрепление убранного фото возвращает его в книгу; вернуть можно и явно
    p = client.patch(f"/api/projects/{pid}/photos/{gone}/flags", json={"excluded": False}, headers=anna).json()
    assert gone in [i for pg in p["layout"]["pages"] for i in pg["photo_ids"]]
    assert client.get(f"/api/projects/{pid}/check", headers=anna).json()["photos"] == 12
    # чужой проект и несуществующее фото недоступны
    assert client.patch(f"/api/projects/{pid}/photos/nope/flags", json={"pinned": True}, headers=anna).status_code == 404
