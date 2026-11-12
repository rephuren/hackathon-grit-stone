"""Сквозной путь на наборе реальных изображений (обложки событий + PNG + поворот EXIF + дубль):
фото → автомакет → правка → Smart Check → TEST-заказ → повтор без дубля → статус.
Запуск: .venv/bin/python scripts/check_newset.py http://localhost:8010 (обложки лежат в frontend/img/events, в репозиторий не входят)."""
import glob, io, json, os, sys, uuid
import httpx
from PIL import Image

BASE = sys.argv[1].rstrip("/") + "/api"
c = httpx.Client(timeout=120)
H = lambda t: {"Authorization": "Bearer " + t}
res = []
def rec(n, ok, ev=""):
    res.append((n, "PASS" if ok else "FAIL", str(ev)[:160])); print(res[-1])

g = c.post(BASE + "/auth/guest").json(); t = g["token"]
prods = c.get(BASE + "/products").json()
book = next(p for p in prods if p["id"] == "classic")
pid = c.post(BASE + "/projects", headers=H(t), json={"product_id": book["id"], "title": "Новый набор"}).json()["id"]

src = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "img", "events", "*.jpg")))
files = []
for i, p in enumerate(src):
    data = open(p, "rb").read()
    im = Image.open(io.BytesIO(data))
    if i == 0:  # PNG
        b = io.BytesIO(); im.convert("RGB").save(b, "PNG"); files.append(("files", ("photo.png", b.getvalue(), "image/png")))
    elif i == 1:  # портрет с EXIF-поворотом 6
        exif = Image.Exif(); exif[0x0112] = 6
        b = io.BytesIO(); im.convert("RGB").save(b, "JPEG", quality=90, exif=exif.tobytes()); files.append(("files", ("rotated.jpg", b.getvalue(), "image/jpeg")))
    else:
        files.append(("files", (os.path.basename(p), data, "image/jpeg")))
files.append(("files", ("dup_" + os.path.basename(src[3]), open(src[3], "rb").read(), "image/jpeg")))  # точный дубль
print("размеры:", sorted({Image.open(io.BytesIO(f[1][1])).size for f in files}))
up = c.post(f"{BASE}/projects/{pid}/photos", headers=H(t), files=files)
j = up.json()
rec("N1 загрузка набора реальных фото (JPEG, PNG, EXIF-поворот)", up.status_code == 200 and j.get("added", 0) >= len(files) - 1, f"код {up.status_code}, отклонено {j.get('rejected')}")
am = c.post(f"{BASE}/projects/{pid}/automaket", headers=H(t), json={})
rec("N2 автомакет", am.status_code == 200, am.status_code)
proj = c.get(f"{BASE}/projects/{pid}", headers=H(t)).json()
ph = proj["photos"]
rot = next((p for p in ph if p.get("orig_name") == "rotated.jpg" or p.get("name") == "rotated.jpg"), None)
rec("N3 EXIF-поворот учтён (портрет)", rot is not None and rot["height"] > rot["width"], rot and (rot["width"], rot["height"]))
ed = c.put(f"{BASE}/projects/{pid}/photos/{ph[2]['id']}/edit", headers=H(t), json={"edits": {"filter": "warm"}})
rec("N4 правка фото сохраняется", ed.status_code == 200, ed.status_code)
ck = c.get(f"{BASE}/projects/{pid}/check", headers=H(t)).json()
codes = sorted({i.get("code") for i in ck["warnings"] + ck["tips"]})
rec("N5 Smart Check находит дубль", len(ck["duplicates"]) >= 1 and "duplicate" in codes, f"группы дублей {ck['duplicates']}")
rec("N5b Smart Check: мало пикселей для формата", "low_resolution" in codes, codes)
pts = c.get(BASE + "/pickup-points").json()
body = {"project_id": pid, "delivery": {"method": "pvz", "pvz_id": pts[0]["id"], "recipient_name": "Тест Тестов", "phone": "+375290000000"}, "promo": "", "accept_warnings": True}
key = "newset-" + uuid.uuid4().hex[:10]
o1 = c.post(BASE + "/orders", headers={**H(t), "Idempotency-Key": key}, json=body)
o2 = c.post(BASE + "/orders", headers={**H(t), "Idempotency-Key": key}, json=body)
rec("N6 TEST-заказ создан", o1.status_code in (200, 201), o1.text[:120])
rec("N7 повтор с тем же ключом = тот же заказ", o1.json().get("id") == o2.json().get("id") and o2.json().get("replayed"), (o1.json().get("id"), o2.json().get("replayed")))
mine = c.get(BASE + "/orders", headers=H(t)).json()
rec("N8 дублей заказа нет", len(mine) == 1, len(mine))
st = c.get(f"{BASE}/orders/{o1.json()['id']}", headers=H(t)).json()
rec("N9 статус виден клиенту", bool(st.get("status")), st.get("status"))
print("\nИТОГ:", sum(r[1] == "PASS" for r in res), "/", len(res))
