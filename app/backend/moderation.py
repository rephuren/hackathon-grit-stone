"""Модерация фото: открытая модель AdamCodd/vit-base-nsfw-detector (ViT, Apache-2.0), полностью на нашем сервере.

Фото никуда не отправляются. Модель только ПОМЕЧАЕТ снимок для ручной проверки оператором и предупреждает клиента,
но не запрещает заказ: автоматическая классификация ошибается в обе стороны, абсолютной точности нет.
Выбор модели проверен на 12 обычных семейных/праздничных фото: оценка 0,004–0,16 (ложных пометок нет);
модель Falconsai/nsfw_image_detection отвергнута — помечала одетую семью с оценкой 0,97.
Необязательно: без transformers/torch или без скачанной модели загрузка фото работает как раньше (проверка = None).
Скачать модель: scripts/fetch_models.py moderation."""
import io
import logging
import os
import threading

log = logging.getLogger("picpac.moderation")

MODEL = os.environ.get("MODERATION_MODEL", "AdamCodd/vit-base-nsfw-detector")
THRESHOLD = float(os.environ.get("MODERATION_THRESHOLD", "0.8"))

_pipe = None
_lock = threading.Lock()
_failed = False


def enabled() -> bool:
    return os.environ.get("MODERATION", "1") == "1"


def available() -> bool:
    """Модель уже скачана и библиотеки есть. Сеть при проверке не используется."""
    if not enabled() or _failed:
        return False
    if _pipe is not None:
        return True
    try:
        import transformers  # noqa: F401
        from huggingface_hub import try_to_load_from_cache
    except Exception:
        return False
    return isinstance(try_to_load_from_cache(MODEL, "config.json"), str)


def _load():
    global _pipe, _failed
    with _lock:
        if _pipe is None and not _failed:
            try:
                from huggingface_hub import snapshot_download
                from transformers import pipeline
                path = snapshot_download(MODEL, local_files_only=True)  # только из кэша: без сети и без скачивания
                _pipe = pipeline("image-classification", model=path, device="cpu")
            except Exception as exc:
                _failed = True
                log.warning("модель модерации не загрузилась: %s", type(exc).__name__)
    return _pipe


def score(image_bytes: bytes):
    """Вероятность «нежелательного» содержимого 0..1 или None, если модерация недоступна."""
    if not available():
        return None
    pipe = _load()
    if pipe is None:
        return None
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        with _lock:  # один поток модели: экономит память, загрузка фото и так идёт по одному файлу
            out = pipe(img)
        return round(float(next((x["score"] for x in out if x["label"].lower() in ("nsfw", "porn", "sexy")), 0.0)), 3)
    except Exception as exc:
        log.warning("модерация фото не выполнена: %s", type(exc).__name__)
        return None


def flagged(q: dict) -> bool:
    return (q or {}).get("nsfw") is not None and q["nsfw"] >= THRESHOLD


def warmup():
    if available():
        _load()
