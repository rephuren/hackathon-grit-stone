"""Каталог, цены, доставка, промокоды, пункты выдачи (mock-данные).

Цены в копейках (целые числа), валюта BYN. Данные СИНТЕТИЧЕСКИЕ: ориентированы на публичные
цены picpac.by, но не являются реальным прайсом. Формулы описаны в README."""
import math
from datetime import date

DPI_MIN = 150  # минимальное разрешение печати, точек на дюйм

PRODUCTS = [
    {"id": "classic", "kind": "book", "title": "Фотокнига Classic",
     "descr": "Плотные страницы, раскрытие на 180°", "min_photos": 10,
     "pages": {"min": 20, "max": 60, "step": 2, "default": 20},
     "options": {"sizes": [
         {"id": "20x20", "label": "20 × 20 см", "w": 20, "h": 20, "base": 8100},
         {"id": "21x30", "label": "21 × 30 см", "w": 21, "h": 30, "base": 9800}],
         "covers": [{"id": "glossy", "label": "Глянцевая"}, {"id": "matte", "label": "Матовая"}],
         "extra_per_2_pages": 300, "base_pages": 20, "production_days": 3}},
    {"id": "graduation", "kind": "book", "title": "Фотокнига «Выпускной»",
     "descr": "Готовая структура для школы и университета", "min_photos": 10,
     "pages": {"min": 20, "max": 60, "step": 2, "default": 20},
     "options": {"sizes": [
         {"id": "20x20", "label": "20 × 20 см", "w": 20, "h": 20, "base": 8500},
         {"id": "21x30", "label": "21 × 30 см", "w": 21, "h": 30, "base": 10300}],
         "covers": [{"id": "glossy", "label": "Глянцевая"}, {"id": "matte", "label": "Матовая"}],
         "extra_per_2_pages": 300, "base_pages": 20, "production_days": 3}},
    {"id": "lite", "kind": "book", "title": "Фотокнига Lite",
     "descr": "Гибкие журнальные страницы, много фото", "min_photos": 12,
     "pages": {"min": 24, "max": 80, "step": 2, "default": 24},
     "options": {"sizes": [{"id": "25x25", "label": "25 × 25 см", "w": 25, "h": 25, "base": 7000}],
                 "covers": [{"id": "glossy", "label": "Глянцевая"}, {"id": "matte", "label": "Матовая"}],
                 "extra_per_2_pages": 200, "base_pages": 24, "production_days": 3}},
    {"id": "premium", "kind": "book", "title": "Фотокнига Premium",
     "descr": "Матовая фотобумага, плотные развороты", "min_photos": 10,
     "pages": {"min": 10, "max": 40, "step": 2, "default": 10},
     "options": {"sizes": [{"id": "21x30", "label": "21 × 30 см", "w": 21, "h": 30, "base": 10050}],
                 "covers": [{"id": "matte", "label": "Матовая"}],
                 "extra_per_2_pages": 500, "base_pages": 10, "production_days": 3}},
    {"id": "calendar", "kind": "calendar", "title": "Настенный календарь",
     "descr": "Обложка и 12 месяцев с вашими фотографиями", "min_photos": 12,
     "pages": {"min": 12, "max": 12, "step": 1, "default": 12},
     "options": {"sizes": [
         {"id": "a4", "label": "A4 · 21 × 30 см", "w": 21, "h": 30, "base": 4500},
         {"id": "30x30", "label": "30 × 30 см", "w": 30, "h": 30, "base": 6500}],
         "covers": [{"id": "spiral", "label": "Пружина"}, {"id": "bar", "label": "Планка"}],
         "production_days": 3}},
    {"id": "print", "kind": "print", "title": "Фотохимическая фотопечать",
     "descr": "Классическая фотохимическая печать, от одной штуки", "min_photos": 1, "pages": None,
     "options": {"sizes": [
         {"id": "10x15", "label": "10 × 15 см", "w": 10, "h": 15, "base": 130},
         {"id": "15x20", "label": "15 × 20 см", "w": 15, "h": 20, "base": 160},
         {"id": "20x30", "label": "20 × 30 см", "w": 20, "h": 30, "base": 320},
         {"id": "30x45", "label": "30 × 45 см", "w": 30, "h": 45, "base": 2000}],
         "covers": [{"id": "glossy", "label": "Глянцевые"}, {"id": "matte", "label": "Матовые"}],
         "production_days": 2}},
    {"id": "print-digital", "kind": "print", "title": "Цифровая фотопечать",
     "descr": "Цифровая печать на фотобумаге, от одной штуки", "min_photos": 1, "pages": None,
     "options": {"sizes": [
         {"id": "10x15", "label": "10 × 15 см", "w": 10, "h": 15, "base": 130},
         {"id": "15x20", "label": "15 × 20 см", "w": 15, "h": 20, "base": 160},
         {"id": "20x30", "label": "20 × 30 см", "w": 20, "h": 30, "base": 320},
         {"id": "30x45", "label": "30 × 45 см", "w": 30, "h": 45, "base": 2000}],
         "covers": [{"id": "glossy", "label": "Глянцевые"}, {"id": "matte", "label": "Матовые"}],
         "production_days": 2}},
]

# Фотокниги по событиям (как раздел picpac.by/fotoknigi-po-sobytiyam). Mock API: шаблон = продукт + оформление страниц.
# Обложки лежат в app/frontend/img/events (материалы picPac, в git не входят); без них показывается цветное превью.
TEMPLATES = [
    {"id": "newyear", "title": "Фотокниги на Новый год", "occasion": "Новый год", "product_id": "classic", "theme": "party", "descr": "Праздничные развороты для зимних кадров", "photos_hint": "20–40 фото", "image": "img/events/newyear.jpg"},
    {"id": "family", "title": "Фотокниги для семьи", "occasion": "Семья", "product_id": "premium", "theme": "soft", "descr": "Семейная история в одной книге", "photos_hint": "20–60 фото", "image": "img/events/family.jpg"},
    {"id": "birthday", "title": "Фотокниги ко дню рождения", "occasion": "День рождения", "product_id": "lite", "theme": "party", "descr": "Яркий коллаж с конфетти", "photos_hint": "10–30 фото", "image": "img/events/birthday.jpg"},
    {"id": "kids", "title": "Фотокниги для детей", "occasion": "Дети", "product_id": "classic", "theme": "kids", "descr": "Звёздочки, облака и мягкие рамки", "photos_hint": "20–50 фото", "image": "img/events/kids.jpg"},
    {"id": "baptism", "title": "Фотокниги на крещение ребёнка", "occasion": "Крещение", "product_id": "premium", "theme": "soft", "descr": "Нежные цвета, крупные кадры", "photos_hint": "15–30 фото", "image": "img/events/baptism.jpg"},
    {"id": "kindergarten", "title": "Выпускные альбомы для детского сада", "occasion": "Детский сад", "product_id": "graduation", "theme": "kids", "descr": "Портреты группы и общие кадры", "photos_hint": "20–40 фото", "image": "img/events/kindergarten.jpg"},
    {"id": "grade4", "title": "Выпускные альбомы для 4 класса", "occasion": "4 класс", "product_id": "graduation", "theme": "classic", "descr": "Начальная школа: класс и учитель", "photos_hint": "20–40 фото", "image": "img/events/grade4.jpg"},
    {"id": "grade9", "title": "Выпускные альбомы для 9 класса", "occasion": "9 класс", "product_id": "graduation", "theme": "classic", "descr": "Строгая классика для класса", "photos_hint": "20–40 фото", "image": "img/events/grade9.jpg"},
    {"id": "grade11", "title": "Выпускные альбомы для 11 класса", "occasion": "11 класс", "product_id": "graduation", "theme": "classic", "descr": "Выпускной: портреты и общие кадры", "photos_hint": "20–40 фото", "image": "img/events/grade11.jpg"},
    {"id": "lovestory", "title": "Фотокниги Love Story", "occasion": "Love Story", "product_id": "premium", "theme": "soft", "descr": "История пары, круглые рамки", "photos_hint": "20–40 фото", "image": "img/events/lovestory.jpg"},
    {"id": "travel", "title": "Фотокниги для путешествий", "occasion": "Путешествия", "product_id": "classic", "theme": "travel", "descr": "Фото-карточки на крафте, как дневник", "photos_hint": "20–60 фото", "image": "img/events/travel.jpg"},
    {"id": "wedding", "title": "Фотокниги на свадьбу", "occasion": "Свадьба", "product_id": "premium", "theme": "soft", "descr": "Нежная палитра, кадры на разворот", "photos_hint": "30–60 фото", "image": "img/events/wedding.jpg"},
    {"id": "family-calendar", "title": "Семейный календарь", "occasion": "Подарок", "product_id": "calendar", "theme": "soft", "descr": "12 месяцев с семейными фото и сеткой дат", "photos_hint": "12 фото", "image": ""},
]

DELIVERY = {
    "pvz": {"label": "Пункт выдачи", "cost": 490},
    "belpost": {"label": "Белпочта", "cost": 590, "free_from": 15000},
    "courier": {"label": "Курьер", "cost": 990},
}

PROMOS = {"TEST10": {"percent": 10}, "FREESHIP": {"free_delivery": True}}

# Условные адреса и координаты (синтетические данные для демонстрации).
PICKUP_POINTS = [
    {"id": "pvz-01", "name": "ПВЗ «Центр»", "address": "пр-т Независимости, 25", "lat": 53.9025, "lon": 27.5590, "hours": "09:00–21:00"},
    {"id": "pvz-02", "name": "ПВЗ «Немига»", "address": "ул. Немига, 5", "lat": 53.9047, "lon": 27.5486, "hours": "10:00–20:00"},
    {"id": "pvz-03", "name": "ПВЗ «Победы»", "address": "пр-т Победителей, 9", "lat": 53.9094, "lon": 27.5748, "hours": "09:00–21:00"},
    {"id": "pvz-04", "name": "ПВЗ «Каменная Горка»", "address": "ул. Притыцкого, 83", "lat": 53.9066, "lon": 27.4328, "hours": "09:00–21:00"},
    {"id": "pvz-05", "name": "ПВЗ «Уручье»", "address": "пр-т Независимости, 185", "lat": 53.9455, "lon": 27.6870, "hours": "10:00–20:00"},
    {"id": "pvz-06", "name": "ПВЗ «Малиновка»", "address": "ул. Володько, 15", "lat": 53.8497, "lon": 27.4740, "hours": "09:00–20:00"},
    {"id": "pvz-07", "name": "ПВЗ «Серебрянка»", "address": "ул. Кижеватова, 60", "lat": 53.8577, "lon": 27.6105, "hours": "10:00–21:00"},
    {"id": "pvz-08", "name": "ПВЗ «Парк Челюскинцев»", "address": "ул. Сурганова, 40", "lat": 53.9217, "lon": 27.6089, "hours": "09:00–20:00"},
]


class PricingError(ValueError):
    pass


def size_of(product: dict, size_id) -> dict:
    sizes = product["options"]["sizes"]
    for s in sizes:
        if s["id"] == size_id:
            return s
    return sizes[0]


def default_settings(product: dict) -> dict:
    o = product["options"]
    key = "cover" if product["kind"] in ("book", "calendar") else "finish"
    s = {"size": o["sizes"][0]["id"], key: o["covers"][0]["id"], "copies": 1}
    if product["kind"] in ("book", "calendar"):
        s["pages"] = product["pages"]["default"]
        s["shape"], s["font"] = "square", "base"  # оформление страниц: форма фото и шрифт подписей
    if product["kind"] == "calendar":
        s["year"] = date.today().year + 1
    return s


SHAPES = ("square", "round", "circle")
FONTS = ("base", "serif", "hand", "type")


def validate_settings(product: dict, settings: dict) -> dict:
    """Возвращает очищенные настройки либо бросает PricingError."""
    o = product["options"]
    out = default_settings(product)
    out.update({k: v for k, v in (settings or {}).items() if k in out})
    if out["size"] not in [s["id"] for s in o["sizes"]]:
        raise PricingError("Неизвестный размер")
    key = "cover" if product["kind"] in ("book", "calendar") else "finish"
    if out[key] not in [c["id"] for c in o["covers"]]:
        raise PricingError("Неизвестная обложка/поверхность")
    try:
        out["copies"] = int(out["copies"])
    except (TypeError, ValueError):
        raise PricingError("Количество должно быть числом")
    if not 1 <= out["copies"] <= 20:
        raise PricingError("Количество: от 1 до 20")
    if product["kind"] in ("book", "calendar"):
        if out["shape"] not in SHAPES:
            raise PricingError("Неизвестная форма фото")
        if out["font"] not in FONTS:
            raise PricingError("Неизвестный шрифт")
        p = product["pages"]
        try:
            out["pages"] = int(out["pages"])
        except (TypeError, ValueError):
            raise PricingError("Число страниц должно быть числом")
        if not (p["min"] <= out["pages"] <= p["max"]) or (out["pages"] - p["min"]) % p["step"]:
            raise PricingError(f"Страниц: от {p['min']} до {p['max']} с шагом {p['step']}")
    if product["kind"] == "calendar":
        try:
            out["year"] = int(out["year"])
        except (TypeError, ValueError):
            raise PricingError("Год календаря должен быть числом")
        current = date.today().year
        if not current <= out["year"] <= current + 10:
            raise PricingError(f"Год календаря: от {current} до {current + 10}")
    return out


def suggest_pages(product: dict, n_photos: int) -> int:
    """«Подобрать по числу фото»: примерно 2,5 фото на страницу, чётное число, в границах продукта."""
    p = product["pages"]
    want = max(p["min"], min(p["max"], math.ceil(n_photos / 2.5)))
    steps = math.ceil((want - p["min"]) / p["step"])
    return min(p["max"], p["min"] + steps * p["step"])


def compute_price(product: dict, settings: dict, n_photos: int, promo=None, delivery="pvz") -> dict:
    """Итоговая цена считается ТОЛЬКО на сервере (МП-04). Суммы в копейках."""
    settings = validate_settings(product, settings)
    o = product["options"]
    size = size_of(product, settings["size"])
    copies = settings["copies"]
    if product["kind"] == "book":
        extra = max(0, settings["pages"] - o["base_pages"]) // 2
        unit = size["base"] + extra * o["extra_per_2_pages"]
        lines = [{"title": f"{product['title']} {size['label']}, {settings['pages']} стр.",
                  "qty": copies, "unit": unit, "sum": unit * copies}]
    elif product["kind"] == "calendar":
        unit = size["base"]
        lines = [{"title": f"{product['title']} {size['label']}, {settings['year']} год · 12 месяцев",
                  "qty": copies, "unit": unit, "sum": unit * copies}]
    else:
        qty = n_photos * copies
        lines = [{"title": f"Печать {size['label']}", "qty": qty, "unit": size["base"], "sum": size["base"] * qty}]
    subtotal = sum(line["sum"] for line in lines)

    promo_code = (promo or "").strip().upper() or None
    discount, free_delivery, promo_error = 0, False, None
    if promo_code:
        rule = PROMOS.get(promo_code)
        if not rule:
            promo_error = "Промокод не найден"
        else:
            if "percent" in rule:
                discount = subtotal * rule["percent"] // 100
            free_delivery = bool(rule.get("free_delivery"))
    if delivery not in DELIVERY:
        raise PricingError("Неизвестный способ получения")
    d = DELIVERY[delivery]
    after = subtotal - discount
    delivery_cost = 0 if (free_delivery or ("free_from" in d and after >= d["free_from"])) else d["cost"]
    total = after + delivery_cost
    return {
        "currency": "BYN", "lines": lines, "subtotal": subtotal, "discount": discount,
        "delivery_label": d["label"], "delivery_cost": delivery_cost, "total": total,
        "promo": promo_code if promo_code and not promo_error else None, "promo_error": promo_error,
        "production_days": o["production_days"],
    }
