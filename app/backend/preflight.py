"""Анализ фото, предпечатная проверка, автомакет.

Проверка качества намеренно сделана классическими алгоритмами (Pillow), а не LLM:
детерминированно, быстро, работает офлайн, тестируется. Модель ИИ подключается там,
где нужна генерация (правки, подписи) — см. README."""
import io
import json
import math
from datetime import datetime

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps, ImageStat

try:  # HEIC с iPhone
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC = True
except Exception:  # pragma: no cover
    HEIC = False

Image.MAX_IMAGE_PIXELS = 80_000_000  # защита от «бомб декомпрессии»
ALLOWED = {"JPEG", "PNG", "MPO", "HEIF", "WEBP", "TIFF", "BMP"}
CONVERTED = {"WEBP", "TIFF", "BMP"}  # принимаем, но пересохраняем в JPEG
BLUR_MIN = 25.0       # дисперсия лапласиана на уменьшенном кадре
BLUR_BAD = 10.0       # «неудачный снимок»: смаз такой, что улучшением не спасти
DARK_MAX = 45.0       # средняя яркость 0..255
DARK_BAD = 28.0
BRIGHT_MAX = 215.0    # пересвет
BRIGHT_BAD = 235.0
OVER_FRAC = 0.25      # доля «выгоревших» пикселей (>=250)
CONTRAST_MIN = 28.0   # стандартное отклонение яркости
NOISE_MAX = 6.0       # средняя разница с медианным фильтром на участке 256×256 в родном разрешении
BPP_MIN = 0.18        # байт на пиксель у JPEG при размере до 1700 px: так выглядят фото, пережатые мессенджером
DUP_CELLS = 0.06      # допустимая доля сильно отличающихся клеток у «дубликатов»
CROP_LOSS = 0.15      # допустимая доля главного объекта, уходящая под обрез
DUP_HAMMING = 4       # расстояние между перцептивными хэшами
SLOT_FACTOR = {"book": 0.6, "calendar": 0.65, "print": 1.0}  # доля длинной стороны страницы, занимаемая фото
LAPLACE = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], 1, 128)


class ImageRejected(Exception):
    pass


NSFW_FLAG = 0.8  # порог пометки для оператора (см. moderation.py); не запрет, а ручная проверка


def _taken_at(img):
    try:
        exif = img.getexif()
        raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)
        if raw:
            return datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S").isoformat()
    except Exception:
        pass
    return None


def _subject_box(gray_small):
    """Грубая оценка «где главный объект»: рамка вокруг основной массы контуров (10–90 % по каждой оси).
    Это эвристика по контрасту, а не распознавание лиц."""
    n = 60
    g = gray_small.resize((64, 64)).filter(ImageFilter.GaussianBlur(1)).filter(ImageFilter.FIND_EDGES).crop((2, 2, 62, 62))
    px = list(g.get_flattened_data() if hasattr(g, "get_flattened_data") else g.getdata())  # края фильтра обрезаны: там мусор
    col, row = [0.0] * n, [0.0] * n
    for i, v in enumerate(px):
        col[i % n] += v
        row[i // n] += v
    if sum(col) < 40:  # почти нет деталей, например, чистое небо
        return [0.0, 0.0, 1.0, 1.0]

    def span(arr):
        tot, acc, lo, hi = sum(arr), 0.0, 0, n - 1
        for i, v in enumerate(arr):
            acc += v
            if acc >= tot * 0.10:
                lo = i
                break
        acc = 0.0
        for i, v in enumerate(arr):
            acc += v
            if acc >= tot * 0.90:
                hi = i
                break
        return lo / n, (hi + 1) / n

    x0, x1 = span(col)
    y0, y1 = span(row)
    return [round(x0, 3), round(y0, 3), round(max(x1, x0 + 0.1), 3), round(max(y1, y0 + 0.1), 3)]


def _metrics(rgb):
    full_gray = rgb.convert("L")
    w, h = full_gray.size
    cx, cy = max(0, (w - 256) // 2), max(0, (h - 256) // 2)
    patch = full_gray.crop((cx, cy, cx + min(256, w), cy + min(256, h)))
    noise = ImageStat.Stat(ImageChops.difference(patch, patch.filter(ImageFilter.MedianFilter(3)))).mean[0]
    gray = full_gray.copy()
    gray.thumbnail((512, 512))
    st = ImageStat.Stat(gray)
    blur = ImageStat.Stat(gray.filter(LAPLACE)).var[0]
    hist = gray.histogram()
    n = sum(hist) or 1
    tiny = gray.resize((8, 8))
    mean = ImageStat.Stat(tiny).mean[0]
    bits = "".join("1" if px > mean else "0" for px in tiny.tobytes())
    q = {"over": round(sum(hist[250:]) / n, 3), "under": round(sum(hist[:6]) / n, 3),
         "t": gray.resize((24, 24), Image.BOX).tobytes().hex(),
         "contrast": round(st.stddev[0], 1), "noise": round(noise, 2), "subject": _subject_box(gray)}
    return round(blur, 2), round(st.mean[0], 1), f"{int(bits, 2):016x}", q


def analyze_image(data: bytes) -> dict:
    """Проверяет файл, снимает EXIF (включая GPS) пересохранением, считает метрики."""
    try:
        probe = Image.open(io.BytesIO(data))
        fmt = probe.format
        probe.verify()
    except Exception as exc:
        raise ImageRejected("Файл повреждён или это не изображение") from exc
    if fmt not in ALLOWED:
        raise ImageRejected(f"Формат {fmt or 'неизвестный'} не подходит для печати. Загрузите JPEG, PNG или HEIC "
                            f"(GIF, SVG, PDF и RAW не принимаем)")
    try:
        img = Image.open(io.BytesIO(data))
        taken = _taken_at(img)
        img = ImageOps.exif_transpose(img)
        alpha = False
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            alpha = img.getchannel("A").getextrema()[0] < 250
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
    except Exception as exc:
        raise ImageRejected("Не удалось прочитать изображение") from exc
    info = encode_variants(img, with_proxy=True)
    info["taken_at"] = taken
    info["q"].update({"fmt": fmt, "alpha": bool(alpha), "bpp": round(len(data) / max(1, img.width * img.height), 3)})
    return info


def encode_variants(img, with_proxy: bool = False) -> dict:
    """Кодирует картинку в JPEG без EXIF (геометки и данные устройства не сохраняются),
    делает миниатюру и считает метрики качества."""
    blur, brightness, ahash, q = _metrics(img)

    def jpeg(im, quality, limit=None):
        if limit:
            im = im.copy()
            im.thumbnail((limit, limit))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
        return buf.getvalue()

    out = {"width": img.width, "height": img.height, "blur": blur, "brightness": brightness, "ahash": ahash, "q": q,
           "main": jpeg(img, 92), "thumb": jpeg(img, 80, 640)}
    if with_proxy:  # уменьшенная копия оригинала для быстрого предпросмотра правок
        out["proxy"] = jpeg(img, 85, 960)
    return out


def merge_q(old, info: dict) -> str:
    """Свежие метрики кадра + сведения об исходном файле (формат, прозрачность, сжатие), которые не пересчитываются."""
    prev = json.loads(old) if old else {}
    q = dict(info["q"])
    for k in ("fmt", "alpha", "bpp", "enh", "nsfw"):  # nsfw: оценка модерации исходника не теряется после правок
        if k in prev:
            q[k] = prev[k]
    return json.dumps(q)


def enhance_image(data: bytes) -> bytes:
    """Автоулучшение: растяжка контраста, подтяжка яркости тёмных кадров, лёгкая резкость."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img = ImageOps.autocontrast(img, cutoff=1)
    mean = ImageStat.Stat(img.convert("L")).mean[0]
    if mean < 90:
        img = ImageEnhance.Brightness(img).enhance(1.35)
    elif mean > 200:  # пересвеченный кадр: слегка притемняем, «выгоревшее» восстановить нельзя
        img = ImageEnhance.Brightness(img).enhance(0.85)
    img = ImageEnhance.Sharpness(img).enhance(1.4)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()


def hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def required_px(product: dict, settings: dict) -> int:
    from .catalog import DPI_MIN, size_of

    size = size_of(product, settings.get("size"))
    long_cm = max(size["w"], size["h"]) * SLOT_FACTOR[product["kind"]]
    return math.ceil(long_cm / 2.54 * DPI_MIN)


SLOT = {"full": 1.0, "side": 0.46, "stack": 2.2, "grid": 1.0}  # отношение сторон окна фото к странице (приблизительно)


def _slot_aspects(product, settings, layout) -> tuple:
    """Приблизительные пропорции окна для каждого фото: по макету (книга) или по формату отпечатка."""
    from .catalog import size_of

    size = size_of(product, settings.get("size"))
    pa = size["w"] / size["h"]
    out = {}
    if product["kind"] in ("book", "calendar") and layout:
        for pg in layout["pages"]:
            for i, pid in enumerate(pg["photo_ids"]):
                if pg["template"] == "one-two":
                    out[pid] = pa * (1.7 if i == 0 else 1.1)
                else:
                    out[pid] = pa * SLOT.get(pg["template"], 1.0)
    return out, pa


def _crop_loss(pr, sr, box):
    """Какая доля рамки «главного объекта» уйдёт под обрез, если вписать кадр с пропорцией pr в окно sr (cover)."""
    x0, y0, x1, y1 = box
    if pr > sr:
        cut = (1 - sr / pr) / 2
        s0, s1 = x0, x1
    else:
        cut = (1 - pr / sr) / 2
        s0, s1 = y0, y1
    lost = max(0.0, min(s1, cut) - s0) + max(0.0, s1 - max(s0, 1 - cut))
    return lost / max(s1 - s0, 1e-6), cut * 2


def _qx(ph) -> dict:
    try:
        return json.loads(ph.get("qx") or "{}")
    except Exception:
        return {}


def bad_reason(ph):
    """Неудачный кадр: настолько смазан, тёмный или «выгоревший», что печатать его не стоит."""
    q = _qx(ph)
    if ph.get("blur") is not None and ph["blur"] < BLUR_BAD and not q.get("enh"):
        return "сильно смазан"
    if ph.get("brightness") is not None:
        if ph["brightness"] < DARK_BAD:
            return "почти чёрный"
        if ph["brightness"] > BRIGHT_BAD or q.get("over", 0) > 0.6:
            return "почти белый, всё пересвечено"
    return None


def _tiny_diff(a, b) -> float:
    """Доля клеток 24×24, заметно различающихся по яркости: отсеивает «тот же фон, но другой кадр»."""
    if not a or not b:
        return 0.0
    x, y = bytes.fromhex(a), bytes.fromhex(b)
    return sum(1 for i, j in zip(x, y) if abs(i - j) > 25) / max(1, len(x))


def find_duplicates(photos: list) -> list:
    """Группы похожих кадров (серии, повторные загрузки). В каждой группе оставляем лучший: резче и больше."""
    ps = sorted(photos, key=lambda p: p["position"])
    parent = {p["id"]: p["id"] for p in ps}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(ps):
        for b in ps[:i]:
            # Хэш 8×8 кодирует только раскладку светлого/тёмного, поэтому для отсева ложных совпадений
            # дополнительно сравниваем яркость и пропорции кадра.
            similar = (abs((a["brightness"] or 0) - (b["brightness"] or 0)) < 15
                       and abs(a["width"] / a["height"] - b["width"] / b["height"]) < 0.05)
            if similar and a["ahash"] and b["ahash"] and hamming(a["ahash"], b["ahash"]) <= DUP_HAMMING \
                    and _tiny_diff(_qx(a).get("t"), _qx(b).get("t")) <= DUP_CELLS:
                parent[root(a["id"])] = root(b["id"])
    groups = {}
    for p in ps:
        groups.setdefault(root(p["id"]), []).append(p)
    out = []
    for g in groups.values():
        if len(g) < 2:
            continue
        best = max(g, key=lambda p: ((p["blur"] or 0) >= BLUR_MIN, p["width"] * p["height"], p["blur"] or 0, -p["position"]))
        out.append({"keep": best["id"], "remove": [p["id"] for p in g if p["id"] != best["id"]]})
    return out


def quality_report(product: dict, settings: dict, photos: list, layout) -> dict:
    """Все замечания по фото. level=warn требует внимания перед заказом, level=tip — совет, заказ не блокирует."""
    req = required_px(product, settings)
    ps = sorted(photos, key=lambda p: p["position"])
    num = {p["id"]: i + 1 for i, p in enumerate(ps)}
    slots, pa = _slot_aspects(product, settings, layout)
    issues = []

    def add(ph, level, code, message, fix):
        issues.append({"photo_id": ph["id"], "level": level, "code": code, "fix": fix, "message": message})

    dups = find_duplicates(ps)
    dup_of = {r: g["keep"] for g in dups for r in g["remove"]}
    for ph in ps:
        q = _qx(ph)
        w, h = ph["width"], ph["height"]
        long_side = max(w, h)
        if long_side < req:
            add(ph, "warn", "low_resolution",
                f"Мало пикселей: {w}×{h}, нужно не менее {req} px по длинной стороне. На печати фото получится нечётким",
                ["replace", "remove"])
        if q.get("enh"):
            add(ph, "tip", "enhanced", "Фото улучшено нейросетью: резкость на печати выше, чем показывает проверка по уменьшенному кадру",
                [])
        elif ph["blur"] is not None and ph["blur"] < BLUR_MIN:
            add(ph, "warn", "blurry", "Фото выглядит размытым", ["replace", "enhance", "remove"])
        if ph["brightness"] is not None and ph["brightness"] < DARK_MAX:
            add(ph, "warn", "too_dark", "Фото слишком тёмное, детали могут потеряться", ["enhance", "replace", "remove"])
        if (ph["brightness"] or 0) > BRIGHT_MAX or q.get("over", 0) > OVER_FRAC:
            add(ph, "warn", "overexposed", "Фото пересвечено: светлые участки «выгорят» и потеряют детали",
                ["enhance", "replace", "remove"])
        elif ph["brightness"] is not None and ph["brightness"] >= DARK_MAX and q.get("contrast", 99) < CONTRAST_MIN:
            add(ph, "tip", "low_contrast", "Фото выглядит блёклым и плоским. «Улучшить» добавит контраста", ["enhance"])
        if q.get("noise", 0) > NOISE_MAX:
            add(ph, "tip", "noisy", "Заметный цифровой шум (съёмка в темноте или высокое ISO). На печати зерно будет видно", ["replace"])
        if q.get("nsfw") is not None and q["nsfw"] >= NSFW_FLAG:
            add(ph, "warn", "moderation",
                "Фото может не пройти модерацию печати. Его посмотрит оператор. Если это ошибка, ничего делать не нужно",
                ["remove"])
        if ph["id"] in dup_of:
            add(ph, "warn", "duplicate",
                f"Похоже на фото {num[dup_of[ph['id']]]}. Лучше оставить то, что чётче и больше", ["remove"])
        box = q.get("subject")
        if box and (box[2] - box[0]) * (box[3] - box[1]) <= 0.5:  # контуры по всему кадру: выделенного объекта нет
            if ph["id"] in slots:
                sr = slots[ph["id"]]
            elif product["kind"] == "print":
                sr = pa if w >= h else 1 / pa
            else:
                sr = None
            if sr:
                lost, cut = _crop_loss(w / h, sr, box)
                if cut > 0.10 and lost > CROP_LOSS:
                    add(ph, "warn", "crop_risk",
                        f"При печати обрежется около {round(cut * 100)}% кадра, возможно, часть главного объекта. "
                        f"Это оценка по контрасту, а не распознавание лиц", ["remove"])
        if w / h > 2.2 or w / h < 0.45:
            add(ph, "tip", "extreme_aspect", "Сильно вытянутый кадр: на странице будут поля или большая обрезка", ["remove"])
        if q.get("fmt") in ("JPEG", "MPO") and q.get("bpp", 1) < BPP_MIN and 800 <= long_side <= 1700:
            add(ph, "tip", "compressed", "Файл сильно сжат (так бывает, если фото пересылали через мессенджер). "
                                         "Если есть оригинал, лучше загрузить его", ["replace"])
        if q.get("alpha"):
            add(ph, "tip", "transparent", "В PNG есть прозрачные области: на печати они станут белыми", [])
        if q.get("fmt") in CONVERTED:
            add(ph, "tip", "format_converted", f"Формат {q['fmt']} пересохранён в JPEG, цвета могут чуть отличаться", [])
    bad = [{"photo_id": p["id"], "reason": r} for p in ps if (r := bad_reason(p))]
    return {"issues": issues, "duplicates": dups, "bad": bad, "required_px": req}


def check_project(product: dict, settings: dict, photos: list, layout) -> dict:
    """Предпечатная проверка. blocking — нельзя заказать, warnings — можно, подтвердив риск, tips — советы."""
    blocking = []
    n = len(photos)
    if n < product["min_photos"]:
        blocking.append({"code": "too_few_photos",
                         "message": f"Добавьте ещё {product['min_photos'] - n} фото (минимум {product['min_photos']})"})
    rep = quality_report(product, settings, photos, layout)
    warnings = [{k: v for k, v in i.items() if k != "level"} for i in rep["issues"] if i["level"] == "warn"]
    tips = [{k: v for k, v in i.items() if k != "level"} for i in rep["issues"] if i["level"] == "tip"]
    if product["kind"] in ("book", "calendar"):
        if not layout:
            blocking.append({"code": "no_layout", "message": "Сначала создайте макет (шаг «Макет»)"})
        else:
            if layout.get("unplaced"):
                blocking.append({"code": "unplaced",
                                 "message": f"{layout['unplaced']} фото не помещаются в макет"})
            blank = sum(1 for pg in layout["pages"] if not pg["photo_ids"])
            if blank:
                warnings.append({"photo_id": None, "code": "blank_pages", "fix": [],
                                 "message": f"Пустых листов: {blank}. Добавьте фотографии"})
    return {"ok": not blocking, "blocking": blocking, "warnings": warnings, "tips": tips,
            "duplicates": rep["duplicates"], "bad": rep["bad"],
            "required_px": rep["required_px"], "photos": n}


THEMES = ("classic", "kids", "travel", "party", "soft")


def build_layout(photos: list, pages: int, sort: str = "date", theme: str = "classic") -> dict:
    """Автомакет: равномерно раскладывает фото по страницам, группируя по дате съёмки."""
    ps = list(photos)
    if sort == "date":
        ps.sort(key=lambda p: (p["taken_at"] or "9999", p["position"]))
    else:
        ps.sort(key=lambda p: p["position"])
    n, out, idx = len(ps), [], 0
    pinned_total = sum(1 for p in ps if p.get("pinned"))
    for i in range(pages):
        if pinned_total:
            # Smart Story: закреплённый кадр занимает свою страницу, остальные делят оставшиеся страницы поровну
            left = n - idx
            if left <= 0:
                count = 0
            elif ps[idx].get("pinned"):
                count = 1
            else:
                pinned_left = sum(1 for p in ps[idx:] if p.get("pinned"))
                free_pages = max(1, pages - i - pinned_left)
                run = 0
                while idx + run < n and not ps[idx + run].get("pinned"):
                    run += 1
                count = max(1, min(4, run, -(-(left - pinned_left) // free_pages)))
        else:
            # фото меньше, чем страниц: по одному на страницу подряд, пустые страницы остаются в конце книги
            count = (1 if i < n else 0) if n < pages else min(4, (i + 1) * n // pages - i * n // pages)
        chunk = ps[idx: idx + count]
        idx += count
        if len(chunk) == 1:
            tpl = "full"
        elif len(chunk) == 2:
            tpl = "stack" if all(p["width"] >= p["height"] for p in chunk) else "side"
        elif len(chunk) == 3:
            tpl = "one-two"
        elif len(chunk) == 4:
            tpl = "grid"
        else:
            tpl = "blank"
        date = (chunk[0]["taken_at"] or "")[:10] if chunk else ""
        out.append({"page": i + 1, "template": tpl, "photo_ids": [p["id"] for p in chunk], "caption": date,
                    "variant": i % 3})  # variant: чередование поворотов и рамок в тематическом оформлении
    return {"pages": out, "unplaced": n - idx, "order": [p["id"] for p in ps], "sort": sort,
            "theme": theme if theme in THEMES else "classic"}
