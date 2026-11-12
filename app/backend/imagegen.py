"""Локальная генерация кадра по описанию (без внешних сервисов, наружу ничего не уходит).

- Перевод описания ru→en: Helsinki-NLP/opus-mt-ru-en (CC-BY-4.0). Модели генерации понимают в основном английский:
  без перевода «мальчик на качелях» превращался в случайный пейзаж.
- Генерация: SimianLuo/LCM_Dreamshaper_v7 (MIT), 4–6 шагов, на GPU Apple (MPS) или CPU.
- Встроенный фильтр недопустимого контента (safety checker) включён: заблокированный кадр не отдаётся.
Модели скачиваются в app/data/models (scripts/fetch_models.py imagegen) и в репозитории не хранятся.
Если библиотек или моделей нет, available() == False и сервер использует запасной внешний сервис."""
import gc
import os
import re
import threading
import time

from . import db as D

_lock = threading.Lock()
_pipe = None
_tr = None
_device = None
_last_used = 0.0
_watch = None
IDLE_MIN = float(os.environ.get("IMAGEGEN_IDLE_MIN", "15"))  # через сколько минут простоя выгрузить модель из памяти
QUEUE_WAIT = float(os.environ.get("IMAGEGEN_QUEUE_WAIT", "90"))  # сколько секунд запрос ждёт своей очереди
_waiting = 0


class Busy(Exception):
    """Очередь слишком длинная: запрос прождал QUEUE_WAIT секунд и не дождался."""


class Blocked(Exception):
    """Фильтр контента забраковал результат."""


def _dir(name):
    return os.path.join(D.data_dir(), "models", name)


def gen_dir():
    return os.environ.get("IMAGEGEN_MODEL") or _dir("lcm-dreamshaper-v7")


def tr_dir():
    return _dir("opus-mt-ru-en")


def available() -> bool:
    if os.environ.get("IMAGEGEN_LOCAL", "1") != "1":
        return False
    if not os.path.exists(os.path.join(gen_dir(), "unet")):
        return False
    try:
        import diffusers  # noqa: F401
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def _torch_device():
    import torch
    if torch.backends.mps.is_available():
        return "mps", torch.float16
    if torch.cuda.is_available():
        return "cuda", torch.float16
    return "cpu", torch.float32


def translate(text: str) -> str:
    """Переводит описание на английский, если в нём есть кириллица. Без модели перевода возвращает исходный текст."""
    global _tr
    if not re.search(r"[А-Яа-яЁё]", text) or not os.path.exists(os.path.join(tr_dir(), "config.json")):
        return text
    try:
        from transformers import MarianMTModel, MarianTokenizer
        if _tr is None:
            _tr = (MarianTokenizer.from_pretrained(tr_dir()), MarianMTModel.from_pretrained(tr_dir()).eval())
        tok, model = _tr
        batch = tok([text], return_tensors="pt", truncation=True, max_length=128)
        out = model.generate(**batch, max_new_tokens=96, max_length=None, num_beams=4)
        return tok.decode(out[0], skip_special_tokens=True).strip() or text
    except Exception:
        return text


# Просьбы вроде «нарисуй мне», «я хочу чтобы» модели генерации не нужны и иногда сбивают её.
_FILLER = re.compile(r"^(please\s+)?(draw|paint|make|create|generate|show|picture|imagine|do)(\s+(me|us|for me))?\s*[:,]?\s*"
                     r"|^i\s+(want|would like|wish)\s+(to\s+(see|have|get)\s+)?(that\s+)?"
                     r"|^(a\s+)?(picture|photo|image|drawing)\s+of\s+", re.I)


def clean_prompt(en: str) -> str:
    s = en.strip().strip(".!")
    for _ in range(3):
        s2 = _FILLER.sub("", s).strip()
        if s2 == s:
            break
        s = s2
    s = re.sub(r"\b(not|don't|do not)\s+(in\s+)?(an?\s+)?anime(\s+style)?\b", "", s, flags=re.I)  # «не в аниме стиле»
    return re.sub(r"\s{2,}", " ", s).strip(" ,.") or en


STYLE = {
    "photo": "realistic photo, natural light, detailed, sharp focus, 35mm",
    "cinema": "cinematic film still, dramatic lighting, shallow depth of field, color graded",
    "illustration": "digital illustration, clean lines, vibrant colors, detailed",
    "retro": "retro 1970s photograph, film grain, warm faded colors",
}
NEGATIVE = ("text, watermark, logo, signature, lowres, blurry, deformed, disfigured, bad anatomy, extra fingers, "
            "extra limbs, ugly, cropped, jpeg artifacts")


_RU_FILLER = re.compile(r"^(пожалуйста[,!]?\s*)?(нарисуй|нарисуйте|сделай|сделайте|создай|создайте|сгенерируй|покажи|изобрази)"
                        r"(\s+(мне|нам|для меня))?\s*[:,]?\s*", re.I)
_RU_WANT = re.compile(r"^(я\s+)?(хочу|хотела бы|хотел бы|мечтаю)[,]?\s+(чтобы|что бы|чтоб)?\s*", re.I)
_RU_NEG = re.compile(r"[,.;]?\s*(сделай(те)?\s+|только\s+)?(не|без)\s+(в\s+)?(стиле\s+)?(аниме|мульт\w*|мультяшн\w*|рисованн\w*|комикс\w*)"
                     r"(\s+стил\w*)?", re.I)
_RU_SLANG = {r"\bморожк\w*|\bмороженк\w*": "мороженое", r"\bфотк\w*": "фото", r"\bдевчонк\w*": "девушка",
             r"\bво\s+круг\b": "вокруг", r"\bкраснобел\w*": "красно-белые"}
# Слова, которые переводчик путает («деда мороза» → «grandfather's frost», «избушка» → «burrow»): подставляем английский
# сразу, переводчик оставляет латиницу как есть.
_RU_GLOSSARY = [
    (r"\bдед\w*\s+мороз\w*", "Санта-Клаус"), (r"\bснегурочк\w*", "Снежная девушка"),
    (r"\bновогодн\w*\s+[её]лк\w*|\b[её]лочк\w*|\b[её]лк\w*", "рождественская ёлка"),
    (r"\bизбушк\w*|\bизб(а|у|ы|е|ой)\b", "деревянный домик"), (r"\bшарик\w*", "ёлочные игрушки"),
    (r"\bмаяк\w*", "lighthouse"), (r"\bсугроб\w*", "сугробы"),
]


def ru_clean(text: str) -> tuple:
    """Готовит русское описание к переводу. Возвращает (текст, дополнительные запреты для модели)."""
    t = " ".join(text.split())
    letters = [ch for ch in t if ch.isalpha()]
    if letters and sum(ch.isupper() for ch in letters) / len(letters) > 0.6:  # КАПС переводчик понимает плохо
        t = t.lower()
    neg = []
    if _RU_NEG.search(t):
        neg.append("anime, cartoon, drawing, illustration, comic")
        t = _RU_NEG.sub("", t)
    for _ in range(2):
        t = _RU_WANT.sub("", _RU_FILLER.sub("", t).strip()).strip()
    for pat, rep in _RU_SLANG.items():
        t = re.sub(pat, rep, t, flags=re.I)
    for pat, rep in _RU_GLOSSARY:
        t = re.sub(pat, rep, t, flags=re.I)
    t = re.sub(r"\b(\w+)\s+\1\b", r"\1", t, flags=re.I)  # «и и» и подобные повторы
    # «я стояла…» → «молодая женщина стояла…», «я сидел…» → «молодой мужчина сидел…»: модели нужен описанный
    # человек, а не «я»; «девушка» переводится как girl и даёт ребёнка, поэтому берём «молодая женщина»
    t = re.sub(r"\bя\s+(\w+ла(сь)?)\b", r"молодая женщина \1", t, flags=re.I)
    t = re.sub(r"\bя\s+(\w+л(ся)?)\b", r"молодой мужчина \1", t, flags=re.I)
    t = re.sub(r"\s+([,.])", r"\1", t).strip(" ,.")
    return (t[:1].upper() + t[1:]) if t else text, ", ".join(neg)


_tok = None


def _fit(en: str, suffix: str, limit: int = 75) -> str:
    """Модель читает только 77 токенов: если описание длинное, обрезаем его по словам, но оставляем стиль в конце."""
    global _tok
    try:
        if _tok is None:
            from transformers import CLIPTokenizer
            _tok = CLIPTokenizer.from_pretrained(os.path.join(gen_dir(), "tokenizer"))
        count = lambda x: len(_tok(x)["input_ids"])  # noqa: E731
    except Exception:
        count = lambda x: int(len(x.split()) * 1.35) + 2  # noqa: E731
    full = f"{en}, {suffix}"
    if count(full) <= limit:
        return full
    words = en.split()
    while words and count(" ".join(words) + ", " + suffix) > limit:
        words.pop()
    return " ".join(words).rstrip(" ,") + ", " + suffix


def build_prompt(prompt: str, style: str) -> tuple:
    """Возвращает (полный запрос для модели, английское описание, дополнительные запреты)."""
    ru, neg = ru_clean(prompt) if re.search(r"[А-Яа-яЁё]", prompt) else (prompt, "")
    en = clean_prompt(translate(ru))
    return _fit(en, STYLE.get(style, STYLE['photo'])), en, neg


def _load():
    global _pipe, _device
    if _pipe is None:
        import torch
        from diffusers import DiffusionPipeline
        dev, dtype = _torch_device()
        pipe = DiffusionPipeline.from_pretrained(gen_dir(), torch_dtype=dtype)
        pipe.to(dev)
        pipe.set_progress_bar_config(disable=True)
        if dev == "mps":
            pipe.enable_attention_slicing()  # меньше пиков памяти на 16 ГБ
        _pipe, _device = pipe, dev
    return _pipe


def _unload():
    global _pipe, _tr
    _pipe, _tr = None, None
    gc.collect()
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:
        pass


def _idle_watch():
    """Освобождает 3–4 ГБ памяти, если генерацией давно не пользовались. Следующий запрос загрузит модель заново."""
    while True:
        time.sleep(60)
        if _pipe is not None and IDLE_MIN > 0 and time.time() - _last_used > IDLE_MIN * 60:
            if _lock.acquire(blocking=False):
                try:
                    _unload()
                finally:
                    _lock.release()


def _touch():
    global _last_used, _watch
    _last_used = time.time()
    if _watch is None:
        _watch = threading.Thread(target=_idle_watch, daemon=True, name="imagegen-idle")
        _watch.start()


def queue_length() -> int:
    return _waiting


def loaded() -> bool:
    return _pipe is not None


def warmup():
    """Загружает модели заранее, чтобы первый пользователь не ждал загрузки весов."""
    if not available():
        return
    with _lock:
        _load()
        _touch()
    translate("тест")


def generate(prompt: str, style: str = "photo", seed=None, size: int = 768):
    """Возвращает (PIL.Image, английский запрос). Бросает Busy, Blocked."""
    import torch
    global _waiting
    # Генерации идут по одной (память), остальные ждут своей очереди, а не получают отказ.
    _waiting += 1
    try:
        got = _lock.acquire(timeout=QUEUE_WAIT)
    finally:
        _waiting -= 1
    if not got:
        raise Busy()
    try:
        _touch()
        full, en, neg = build_prompt(prompt, style)
        pipe = _load()
        g = torch.Generator("cpu").manual_seed(int(seed if seed is not None else torch.seed() % 2**31))
        import inspect
        kw = {"negative_prompt": NEGATIVE + (", " + neg if neg else "")} if "negative_prompt" in inspect.signature(pipe.__call__).parameters else {}
        res = pipe(prompt=full, num_inference_steps=6, guidance_scale=8.0,
                   width=size, height=size, generator=g, output_type="pil", **kw)
        nsfw = getattr(res, "nsfw_content_detected", None)
        if nsfw and any(nsfw):
            raise Blocked()
        _touch()
        return res.images[0], en
    finally:
        _lock.release()
