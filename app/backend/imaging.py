"""Неразрушающие правки фото: кадр, поворот, свет, фильтры, текст, замена фона.

Оригинал всегда сохраняется, а правки хранятся как небольшой словарь параметров (edits) и
накладываются заново при каждом сохранении. Так любую правку можно отменить, а «до/после» честное.
Всё на Pillow: бесплатно и без внешних сервисов."""
import os
import random

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat

FILTERS = ["none", "warm", "cool", "film", "bw", "vivid", "retro", "cinema", "illustration"]
RATIOS = {"orig": None, "1:1": 1.0, "4:5": 0.8, "3:2": 1.5, "2:3": 2 / 3, "16:9": 16 / 9}
TEXT_COLORS = {"white": ((255, 255, 255), (17, 17, 17)), "black": ((17, 17, 17), (255, 255, 255)),
               "yellow": ((255, 212, 0), (17, 17, 17))}
BG_COLORS = {"white": (255, 255, 255), "yellow": (255, 212, 0), "black": (17, 17, 17)}
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
]


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def sanitize(e) -> dict:
    """Оставляет только известные поля и ограничивает значения. Пустой результат = «без правок»."""
    out: dict = {}
    if not isinstance(e, dict):
        return out
    if e.get("rotate") in (90, 180, 270):
        out["rotate"] = e["rotate"]
    if e.get("flip") is True:
        out["flip"] = True
    c = e.get("crop")
    if isinstance(c, dict):
        ratio = c.get("ratio", "orig") if c.get("ratio", "orig") in RATIOS else "orig"
        zoom = _clamp(_num(c.get("zoom"), 1.0), 1.0, 4.0)
        if ratio != "orig" or zoom > 1.0001:
            out["crop"] = {"ratio": ratio, "zoom": round(zoom, 3),
                           "x": round(_clamp(_num(c.get("x"), 0.5), 0, 1), 3),
                           "y": round(_clamp(_num(c.get("y"), 0.5), 0, 1), 3)}
    if e.get("filter") in FILTERS and e["filter"] != "none":
        out["filter"] = e["filter"]
    a = e.get("adjust")
    if isinstance(a, dict):
        vals = {k: int(_clamp(_num(a.get(k)), -100, 100)) for k in ("brightness", "contrast", "saturation")}
        if any(vals.values()):
            out["adjust"] = vals
    if e.get("auto") is True:
        out["auto"] = True
    t = e.get("text")
    if isinstance(t, dict) and str(t.get("value", "")).strip():
        out["text"] = {"value": "".join(ch for ch in str(t["value"]) if ch.isprintable())[:60].strip(),
                       "pos": t.get("pos") if t.get("pos") in ("top", "center", "bottom") else "bottom",
                       "color": t.get("color") if t.get("color") in TEXT_COLORS else "white",
                       "size": int(_clamp(_num(t.get("size"), 7), 3, 14))}
        if not out["text"]["value"]:
            del out["text"]
    b = e.get("bg")
    if isinstance(b, dict) and (b.get("mode") in BG_COLORS or b.get("mode") == "blur"):
        out["bg"] = {"mode": b["mode"]}
    return out


def _curve(fr, fg, fb):
    clip = lambda v: max(0, min(255, int(v)))
    return [clip(fr(i)) for i in range(256)] + [clip(fg(i)) for i in range(256)] + [clip(fb(i)) for i in range(256)]


def _grain(img, strength=0.3, seed=7):
    random.seed(seed)
    noise = Image.effect_noise(img.size, 28).convert("RGB")
    return Image.blend(img, ImageChops.overlay(img, noise), strength)


def _vignette(img, darkness=0.55):
    grad = Image.radial_gradient("L").resize(img.size)
    return Image.composite(ImageEnhance.Brightness(img).enhance(darkness), img, grad)


def apply_filter(img, name):
    if name == "warm":
        return ImageEnhance.Color(img.point(_curve(lambda i: i * 1.08, lambda i: i, lambda i: i * 0.9))).enhance(1.05)
    if name == "cool":
        return img.point(_curve(lambda i: i * 0.92, lambda i: i, lambda i: i * 1.08))
    if name == "film":
        img = img.point(_curve(lambda i: 18 + i * 0.9 * 1.03, lambda i: 18 + i * 0.9, lambda i: 18 + i * 0.9 * 0.96))
        img = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(0.85)).enhance(1.06)
        return _grain(img, 0.25)
    if name == "bw":
        gray = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1)
        return ImageEnhance.Contrast(gray.convert("RGB")).enhance(1.1)
    if name == "vivid":
        return ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.4)).enhance(1.1)
    if name == "retro":
        sepia = ImageOps.colorize(ImageOps.grayscale(img), black=(40, 20, 10), mid=(150, 100, 60), white=(255, 235, 200))
        return _grain(_vignette(Image.blend(img, sepia, 0.65), 0.6), 0.3)
    if name == "cinema":
        img = img.point(_curve(lambda i: i * 1.12 if i > 128 else i * 0.88, lambda i: i,
                               lambda i: i * 0.88 if i > 128 else i * 1.12))
        return ImageEnhance.Contrast(img).enhance(1.12)
    if name == "illustration":
        big = max(img.size)
        work = img if big <= 1400 else img.resize((round(img.width * 1400 / big), round(img.height * 1400 / big)))
        smooth = ImageOps.posterize(work.filter(ImageFilter.MedianFilter(5)), 4)
        edges = ImageOps.autocontrast(ImageOps.invert(ImageOps.grayscale(work).filter(ImageFilter.FIND_EDGES)))
        out = ImageEnhance.Color(Image.blend(smooth, ImageChops.multiply(smooth, edges.convert("RGB")), 0.5)).enhance(1.3)
        return out if work is img else out.resize(img.size)
    return img


def auto_enhance(img):
    img = ImageOps.autocontrast(img, cutoff=1)
    mean = ImageStat.Stat(img.convert("L")).mean[0]
    if mean < 90:
        img = ImageEnhance.Brightness(img).enhance(1.35)
    elif mean > 200:  # пересвет: чуть притемняем, «выгоревшие» детали не вернуть
        img = ImageEnhance.Brightness(img).enhance(0.85)
    return ImageEnhance.Sharpness(img).enhance(1.4)


def _crop(img, c):
    w, h = img.size
    ratio = RATIOS[c["ratio"]] or (w / h)
    bw, bh = (h * ratio, h) if w / h > ratio else (w, w / ratio)
    cw, ch = max(1.0, bw / c["zoom"]), max(1.0, bh / c["zoom"])
    left = _clamp(c["x"] * w - cw / 2, 0, w - cw)
    top = _clamp(c["y"] * h - ch / 2, 0, h - ch)
    return img.crop((round(left), round(top), round(left + cw), round(top + ch)))


def _font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def _text(img, t):
    w, h = img.size
    size = max(12, int(w * t["size"] / 100))
    draw = ImageDraw.Draw(img)
    font = _font(size)
    while size > 12 and draw.textlength(t["value"], font=font) > w * 0.92:
        size -= max(1, size // 10)
        font = _font(size)
    fill, stroke = TEXT_COLORS[t["color"]]
    y = {"top": h * 0.08 + size / 2, "center": h / 2, "bottom": h * 0.92 - size / 2}[t["pos"]]
    draw.text((w / 2, y), t["value"], font=font, fill=fill, anchor="mm", stroke_width=max(1, size // 12), stroke_fill=stroke)
    return img


def _background(img, mask, mode):
    bg = img.filter(ImageFilter.GaussianBlur(max(img.size) / 60)) if mode == "blur" else Image.new("RGB", img.size, BG_COLORS[mode])
    return Image.composite(img, bg, mask.resize(img.size))


def render(img, edits, mask=None):
    """Применяет правки к картинке и возвращает новую RGB-картинку. Порядок: фон → поворот → кадр → свет → фильтр → текст."""
    e = sanitize(edits)
    img = img.convert("RGB")
    if "bg" in e and mask is not None:
        img = _background(img, mask, e["bg"]["mode"])
    if "rotate" in e:
        img = img.rotate(-e["rotate"], expand=True)
    if e.get("flip"):
        img = ImageOps.mirror(img)
    if "crop" in e:
        img = _crop(img, e["crop"])
    if e.get("auto"):
        img = auto_enhance(img)
    if "adjust" in e:
        a = e["adjust"]
        img = ImageEnhance.Brightness(img).enhance(1 + a["brightness"] / 100 * 0.6)
        img = ImageEnhance.Contrast(img).enhance(1 + a["contrast"] / 100 * 0.6)
        img = ImageEnhance.Color(img).enhance(1 + a["saturation"] / 100)
    if "filter" in e:
        img = apply_filter(img, e["filter"])
    if "text" in e:
        img = _text(img.copy(), e["text"])
    return img
