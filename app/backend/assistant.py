"""ИИ-помощник в приложении: архитектура под подключение платформы Mary (maryrose.by).

Схема: интерфейс → POST /api/assistant → provider.answer(message, context) → {reply, actions}.
Провайдеры взаимозаменяемы:
  * DemoProvider — работает всегда, без сети и без платных API: правила по ключевым словам + данные пользователя
    (проекты, заказы, каталог). Честно помечается как демо-режим.
  * MaryProvider — включается, только когда заданы MARY_API_URL и MARY_API_KEY (официальные доступы от Mary/picPac).
    Отправляем короткий обезличенный контекст: без фото, имён, телефонов и адресов. При ошибке или таймауте
    отвечает DemoProvider, пользователь не остаётся без ответа.
Действия (actions) — только ссылки внутри приложения: помощник ничего не заказывает и не удаляет сам.
"""
import os
import re

import httpx

STATUS_RU = {"accepted": "принят", "moderation": "на проверке макета", "production": "в производстве",
             "ready": "готов и передан в доставку", "completed": "завершён", "rejected": "отклонён"}


def _money(kop: int) -> str:
    return f"{kop / 100:.2f}".replace(".", ",") + " BYN"


class DemoProvider:
    name = "demo"

    RULES = [
        ("order", r"заказ|статус|где мой|трек|доставк[аи] заказ"),
        ("photos", r"сколько фото|минимум|мало фото|страниц"),
        ("price", r"цен|стоит|стоимост|сколько стоит|дорого"),
        ("promo", r"промокод|скидк|акци"),
        ("delivery", r"доставк|пункт выдачи|пвз|курьер|белпочт|самовывоз"),
        ("time", r"срок|когда будет|сколько дней|как долго"),
        ("calendar", r"календар"),
        ("book", r"фотокниг|альбом|книг"),
        ("print", r"печат|распечат"),
        ("quality", r"качеств|размыт|темн|улучш|чётк|четк"),
        ("templates", r"шаблон|свадьб|выпускн|оформлен|тем[аы]"),
        ("data", r"удал(ить|и) (мои )?данн|персональн|аккаунт"),
        ("support", r"поддержк|оператор|человек|жалоб|проблем"),
        ("hello", r"^(привет|здравств|добр|хай|hello|hi)\b"),
    ]

    def answer(self, message: str, ctx: dict) -> dict:
        text = (message or "").lower().strip()
        intent = next((name for name, rx in self.RULES if re.search(rx, text)), "help")
        return getattr(self, "_" + intent)(ctx)

    # --- ответы ---
    def _order(self, ctx):
        orders = ctx.get("orders") or []
        if not orders:
            return {"reply": "Заказов пока нет. Соберите фотокнигу, календарь или печать и оформите тестовый заказ.",
                    "actions": [{"label": "Создать проект", "href": "#/new"}]}
        o = orders[0]
        return {"reply": f"Последний заказ {o['number']}: {STATUS_RU.get(o['status'], o['status'])}, сумма {_money(o['total'])}. "
                         "Подробности, состав и обращения — в карточке заказа.",
                "actions": [{"label": f"Открыть заказ {o['number']}", "href": f"#/order/{o['id']}"},
                            {"label": "Все заказы", "href": "#/orders"}]}

    def _photos(self, ctx):
        d = ctx.get("draft")
        if d:
            need = max(0, d["min_photos"] - d["photos"])
            tail = f"Добавьте ещё {need}." if need else "Фото хватает, можно собирать макет."
            return {"reply": f"В проекте «{d['title']}» сейчас {d['photos']} фото, для продукта «{d['product']}» нужно минимум {d['min_photos']}. {tail} "
                             "Число страниц задаётся отдельно: фото раскладываются по страницам автоматически.",
                    "actions": [{"label": "Открыть проект", "href": f"#/project/{d['id']}/photos"}]}
        return {"reply": "Фотокниге нужно минимум 10 фото, календарю 12 (по одному на месяц), печати — от одного снимка.",
                "actions": [{"label": "Выбрать продукт", "href": "#/new"}]}

    def _price(self, ctx):
        lines = [f"{p['title']} — от {_money(p['from'])}" for p in ctx.get("products", [])][:6]
        return {"reply": "Цены считаются на сервере и зависят от формата, страниц и количества:\n" + "\n".join(lines),
                "actions": [{"label": "Открыть каталог", "href": "#/new"}]}

    def _promo(self, ctx):
        return {"reply": "Тестовые промокоды: TEST10 даёт скидку 10%, FREESHIP — бесплатную доставку. Вводятся при оформлении.", "actions": []}

    def _delivery(self, ctx):
        return {"reply": "Способы получения: пункт выдачи 4,90 BYN, Белпочта 5,90 BYN (бесплатно от 150 BYN), курьер 9,90 BYN. "
                         "Пункт выдачи можно выбрать на карте при оформлении.", "actions": []}

    def _time(self, ctx):
        return {"reply": "Производство фотокниги и календаря — 3 рабочих дня, печать фото — 2. Ориентировочная дата показывается при оформлении.", "actions": []}

    def _calendar(self, ctx):
        return {"reply": "Настенный календарь: обложка и 12 месяцев с сеткой дат, нужно 12 фото, форматы A4 и 30×30. Год выбирается в параметрах.",
                "actions": [{"label": "Создать календарь", "href": "#/new?kind=calendar"}]}

    def _book(self, ctx):
        return {"reply": "Фотокнига собирается автоматически: загрузите от 10 фото, я разложу их по страницам и предложу оформление. Можно выбрать готовый шаблон.",
                "actions": [{"label": "Создать фотокнигу", "href": "#/new"}]}

    def _print(self, ctx):
        return {"reply": "Печать фото — от одного снимка, форматы от 10×15 до 30×45 см, глянец или мат.",
                "actions": [{"label": "Печать фото", "href": "#/new?kind=print"}]}

    def _quality(self, ctx):
        return {"reply": "Перед заказом я проверяю разрешение, резкость, свет и дубликаты. Тёмные и мелкие фото можно улучшить в редакторе (вкладка AI) или заменить.",
                "actions": []}

    def _templates(self, ctx):
        return {"reply": "Есть готовые шаблоны: свадьба, выпускной, детский альбом, путешествие, праздник. Выберите карточку в каталоге — оформление применится при сборке макета.",
                "actions": [{"label": "Шаблоны", "href": "#/new"}]}

    def _data(self, ctx):
        return {"reply": "Удалить фото и данные можно в Профиле: «Удалить мои данные». Я сам ничего не удаляю.",
                "actions": [{"label": "Профиль", "href": "#/profile"}]}

    def _support(self, ctx):
        return {"reply": "Обращение в поддержку создаётся из карточки заказа: так оператор сразу видит, о каком заказе речь.",
                "actions": [{"label": "Мои заказы", "href": "#/orders"}]}

    def _hello(self, ctx):
        return {"reply": "Привет! Я Робо. Спросите про статус заказа, цены, сроки, сколько нужно фото или как выбрать шаблон.", "actions": []}

    def _help(self, ctx):
        return {"reply": "Пока я умею отвечать на вопросы о заказах, ценах, сроках, доставке, количестве фото и шаблонах. "
                         "Сложные вопросы передайте оператору из карточки заказа.",
                "actions": [{"label": "Где мой заказ?", "ask": "Где мой заказ?"},
                            {"label": "Сколько нужно фото?", "ask": "Сколько нужно фото?"},
                            {"label": "Сколько стоит?", "ask": "Сколько стоит?"}]}


class MaryProvider:
    """Подключение к Mary по официальному API. Формат запроса/ответа — предположение до получения документации."""
    name = "mary"

    def __init__(self, url: str, key: str):
        self.url, self.key = url, key

    def answer(self, message: str, ctx: dict) -> dict:
        safe = {"draft": ctx.get("draft") and {k: ctx["draft"][k] for k in ("product", "photos", "min_photos")},
                "orders": [{"status": o["status"], "total": o["total"]} for o in (ctx.get("orders") or [])[:3]]}
        r = httpx.post(self.url, json={"message": message[:500], "context": safe, "channel": "picpac-app"},
                       headers={"Authorization": f"Bearer {self.key}"}, timeout=8)
        r.raise_for_status()
        data = r.json()
        actions = [a for a in data.get("actions", []) if str(a.get("href", "#")).startswith("#/")]
        return {"reply": str(data.get("reply", ""))[:1500], "actions": actions[:4]}


def provider():
    url, key = os.environ.get("MARY_API_URL", "").strip(), os.environ.get("MARY_API_KEY", "").strip()
    return MaryProvider(url, key) if url and key else DemoProvider()


def ask(message: str, ctx: dict) -> dict:
    p = provider()
    try:
        out = p.answer(message, ctx)
        mode = p.name
    except Exception:  # Mary недоступна: отвечаем локально и честно это показываем
        out = DemoProvider().answer(message, ctx)
        mode = "demo-fallback"
    return {**out, "provider": mode}
