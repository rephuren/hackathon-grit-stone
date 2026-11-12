"""Удаление объектов с фото: нейросеть LaMa (Apache-2.0, ONNX) через onnxruntime, полностью локально.

Пользователь закрашивает кистью лишнее, модель дорисовывает фон. Обрабатывается только квадрат вокруг закрашенного
места (модель работает с 512×512), результат вставляется обратно в полноразмерное фото, поэтому остальной кадр
остаётся в исходном разрешении. Без модели кнопка скрыта (см. scripts/fetch_models.py)."""
import os
import threading

from PIL import Image, ImageFilter

from . import db as D

SIZE = 512
_session = None
_lock = threading.Lock()


class Busy(Exception):
    pass


def model_path() -> str:
    return os.environ.get("INPAINT_MODEL") or os.path.join(D.data_dir(), "models", "lama_fp32.onnx")


def available() -> bool:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return os.path.exists(model_path())


def _session_get():
    global _session
    if _session is None:
        import onnxruntime as ort

        _session = ort.InferenceSession(model_path(), providers=["CPUExecutionProvider"])
    return _session


def _run_patch(patch: Image.Image, hole: Image.Image) -> Image.Image:
    """patch — RGB любого размера, hole — L (255 = убрать). Возвращает RGB того же размера."""
    import numpy as np

    sess = _session_get()
    w, h = patch.size
    a = np.asarray(patch.resize((SIZE, SIZE), Image.LANCZOS), dtype=np.float32)[None].transpose(0, 3, 1, 2) / 255.0
    m = (np.asarray(hole.resize((SIZE, SIZE), Image.BILINEAR), dtype=np.float32) > 127)[None, None].astype(np.float32)
    names = [i.name for i in sess.get_inputs()]
    out = sess.run(None, {names[0]: a.astype(np.float32), names[1]: m})[0][0]
    out = out.transpose(1, 2, 0)
    if float(out.max()) <= 1.5:  # у разных экспортов выход в 0..1 или 0..255
        out = out * 255.0
    res = Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGB")
    return res.resize((w, h), Image.LANCZOS)


def erase(img: Image.Image, mask: Image.Image) -> Image.Image:
    """Убирает закрашенные области. mask — L того же размера, что img (255 = убрать)."""
    if not _lock.acquire(blocking=False):
        raise Busy()
    try:
        img = img.convert("RGB")
        mask = mask.convert("L").resize(img.size, Image.BILINEAR).point(lambda v: 255 if v > 100 else 0)
        mask = mask.filter(ImageFilter.MaxFilter(9))  # захватываем ореол и тень вокруг мазка
        box = mask.getbbox()
        if not box:
            return img
        W, H = img.size
        bw, bh = box[2] - box[0], box[3] - box[1]
        side = min(max(int(max(bw, bh) * 2.4), 384), min(W, H))
        cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
        x0 = max(0, min(W - side, cx - side // 2))
        y0 = max(0, min(H - side, cy - side // 2))
        region = (x0, y0, x0 + side, y0 + side)
        patch, hole = img.crop(region), mask.crop(region)
        filled = _run_patch(patch, hole)
        soft = hole.filter(ImageFilter.GaussianBlur(3))  # мягкий шов, чтобы вставка не выделялась
        out = img.copy()
        out.paste(Image.composite(filled, patch, soft), (x0, y0))
        return out
    finally:
        _lock.release()
