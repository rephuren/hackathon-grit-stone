"""Нейросетевое увеличение и улучшение фото: Real-ESRGAN (realesr-general-x4v3, BSD-3-Clause) через onnxruntime.
Полностью локально, фото никуда не отправляется. Модель ~5 МБ: scripts/fetch_models.py."""
import os
import threading

from PIL import Image, ImageFilter

from . import db as D

NAME = "realesr-general-x4v3.onnx"
TILE, PAD = 192, 12          # обработка плитками, чтобы не раздувать память на больших фото
MAX_IN_SIDE = 3200           # если длинная сторона уже больше, увеличивать нет смысла
MAX_OUT_SIDE = 7000
_session = None
_lock = threading.Lock()     # одно увеличение за раз: оно нагружает процессор


def model_path() -> str:
    return os.environ.get("UPSCALE_MODEL") or os.path.join(D.data_dir(), "models", NAME)


def available() -> bool:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return os.path.exists(model_path())


class Busy(Exception):
    pass


def run(img: Image.Image, scale: int = 2) -> Image.Image:
    """Возвращает увеличенную в 2 или 4 раза картинку. Бросает Busy, если уже идёт другое увеличение."""
    import numpy as np
    import onnxruntime as ort

    global _session
    if not _lock.acquire(blocking=False):
        raise Busy()
    try:
        if _session is None:
            so = ort.SessionOptions()
            so.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
            _session = ort.InferenceSession(model_path(), so, providers=["CPUExecutionProvider"])
        name = _session.get_inputs()[0].name
        src = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
        h, w, _ = src.shape
        out = np.zeros((h * 4, w * 4, 3), dtype=np.float32)
        for y in range(0, h, TILE):
            for x in range(0, w, TILE):
                y0, x0 = max(0, y - PAD), max(0, x - PAD)
                y1, x1 = min(h, y + TILE + PAD), min(w, x + TILE + PAD)
                tile = src[y0:y1, x0:x1].transpose(2, 0, 1)[None]
                res = _session.run(None, {name: np.ascontiguousarray(tile)})[0][0].transpose(1, 2, 0)
                oy, ox = (y - y0) * 4, (x - x0) * 4
                th, tw = min(TILE, h - y) * 4, min(TILE, w - x) * 4
                out[y * 4: y * 4 + th, x * 4: x * 4 + tw] = res[oy: oy + th, ox: ox + tw]
        big = Image.fromarray((np.clip(out, 0, 1) * 255.0 + 0.5).astype("uint8"), "RGB")
        if scale == 2:
            big = big.resize((w * 2, h * 2), Image.LANCZOS)
        return big.filter(ImageFilter.UnsharpMask(radius=1.6, percent=55, threshold=2))  # доводка: на печати видна сильнее, чем на экране
    finally:
        _lock.release()
