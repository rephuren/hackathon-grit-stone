"""Загрузка app/.env (без внешних библиотек). Уже заданные переменные окружения не перезаписываются."""
import os

_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
if os.path.exists(_env):
    with open(_env, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.split("  #")[0].strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
