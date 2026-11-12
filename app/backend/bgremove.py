"""Удаление фона: небольшая модель U²-Net (u2netp, Apache-2.0) через onnxruntime, полностью локально.

Модель и onnxruntime необязательны: если их нет, приложение работает, а кнопка «Убрать фон» скрыта.
Установка: см. scripts/fetch_models.py."""
import os

from PIL import Image

from . import db as D

_session = None


def model_path() -> str:
    return os.environ.get("BG_MODEL") or os.path.join(D.data_dir(), "models", "u2netp.onnx")


def available() -> bool:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return os.path.exists(model_path())


def make_mask(img: Image.Image) -> Image.Image:
    """Возвращает маску переднего плана (L, 255 = объект) того же размера, что и картинка."""
    import numpy as np
    import onnxruntime as ort

    global _session
    if _session is None:
        _session = ort.InferenceSession(model_path(), providers=["CPUExecutionProvider"])
    w, h = img.size
    a = np.asarray(img.resize((320, 320), Image.BILINEAR), dtype=np.float32)
    a = a / max(float(a.max()), 1e-6)
    a = (a - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array([0.229, 0.224, 0.225], dtype=np.float32)
    a = a.transpose(2, 0, 1)[None].astype(np.float32)
    out = _session.run(None, {_session.get_inputs()[0].name: a})[0][0, 0]
    out = (out - out.min()) / (out.max() - out.min() + 1e-8)
    out = np.clip((out - 0.35) * 2.5, 0, 1)  # чуть жёстче граница объекта
    return Image.fromarray((out * 255).astype("uint8"), "L").resize((w, h), Image.LANCZOS)
