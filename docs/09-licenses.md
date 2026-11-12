# Библиотеки, модели и лицензии (ОТ-08)

Всё бесплатно и открыто. Версии взяты из `app/requirements.txt` и из установленных пакетов (`importlib.metadata`).
Лицензии моделей указаны по репозиториям-источникам; **перед сдачей их стоит ещё раз открыть и сверить**, мы это не автоматизировали.

## Python (сервер)
| Пакет | Версия | Лицензия |
|---|---|---|
| FastAPI | 0.162.4 | MIT |
| Starlette | 1.10.0 | BSD-3-Clause |
| Pydantic | 2.14.3 | MIT |
| Uvicorn | 0.54.0 | BSD-3-Clause |
| python-multipart | 0.0.33 | Apache-2.0 |
| Pillow | 13.0.0 | MIT-CMU (HPND) |
| pillow-heif | 1.8.1 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| pytest (только тесты) | 9.2.0 | MIT |
| onnxruntime (нейросетевые функции) | 1.31.1 | MIT |
| NumPy (нейросетевые функции) | 2.5.4 | BSD-3-Clause и совместимые |
| PyTorch (локальная генерация) | 2.15.0 | BSD-3-Clause и совместимые (Apache-2.0, MIT и др.) |
| Diffusers | 0.42.1 | Apache-2.0 |
| Transformers | 5.22.2 | Apache-2.0 |
| Accelerate | 1.15.0 | Apache-2.0 |
| safetensors | 0.8.0 | Apache-2.0 |
| huggingface_hub (загрузка моделей) | 2.4.0 | Apache-2.0 |
| SentencePiece | 0.2.3 | Apache-2.0 |
| sacremoses | 0.2.0 | MIT |

## Интерфейс
| Что | Лицензия |
|---|---|
| Leaflet (`frontend/vendor`, карта ПВЗ) | BSD-2-Clause |
| Карта и данные OpenStreetMap | ODbL; обязательна подпись «© OpenStreetMap» (есть на карте) |
| Telegram Web App SDK (подгружается с telegram.org) | MIT |
| Иконки, маскоты (`img/*.svg`) | собственные/нарисованные для прототипа; прав на бренд «Фотопринт» нет |

## Модели (скачиваются `app/scripts/fetch_models.py`, в репозитории не хранятся)
| Назначение | Модель | Источник | Лицензия (по источнику) |
|---|---|---|---|
| Увеличение разрешения | Real-ESRGAN general-x4v3 (ONNX) | huggingface.co/CoderViking/realesr-general-x4v3-onnx | BSD-3-Clause (оригинал xinntao/Real-ESRGAN) |
| Удаление фона | U²-Net-p (`u2netp.onnx`) | github.com/danielgatis/rembg | Apache-2.0 |
| Удаление объекта | LaMa (ONNX) | huggingface.co/Carve/LaMa-ONNX | Apache-2.0 (оригинал advimman/lama) |
| Генерация кадра по описанию | LCM Dreamshaper v7 (+ встроенный фильтр контента) | huggingface.co/SimianLuo/LCM_Dreamshaper_v7 | MIT (по карточке модели) |
| Перевод описания ru→en | opus-mt-ru-en | huggingface.co/Helsinki-NLP/opus-mt-ru-en | CC-BY-4.0 (нужно указание авторства: Helsinki-NLP, OPUS-MT) |

## Шрифт и логотип
- Шрифт Manrope (Mikhail Sharanda), SIL Open Font License 1.1: лежит локально в `app/frontend/fonts/`, внешние CDN не нужны.
- Логотип PicPac (`img/logo*.png`) вырезан из «БРЕНДБУК Pic Pac.pdf» (стр. 7), материал заказчика; используется только в прототипе хакатона. Файл-оригинал (SVG/PNG) у заказчика не запрошен, поэтому растр 520 px.
- Персонажи Робо и кот: оригинальные SVG, нарисованы для проекта (референс стиля — скриншот команды, ничего не копировалось).

## Внешние сервисы
- Генерация картинок по описанию: **по умолчанию локально** (модели выше, наружу ничего не уходит). Если локальной модели нет или задано `IMAGEGEN_LOCAL=0`, используется запасной бесплатный **Pollinations**: уходит только текст запроса (уже переведённый), фото не отправляются; ограничение сервиса 1 картинка в 45 секунд на адрес, на картинке может быть его водяной знак. Внешний вариант отключается `AI_GENERATE=0`.
- Telegram Bot API (если задан токен бота).
