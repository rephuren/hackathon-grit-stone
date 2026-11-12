# Архитектура и альтернативы

```
Telegram Mini App / мобильный браузер
        │  HTTPS, JSON, Bearer-токен
        ▼
FastAPI ── auth/роли/аудит ── каталог+цены ── проверка фото+автомакет ── заказы/статусы/поддержка
        │                                                    │
     SQLite                                        файлы на диске (подписанные ссылки)
        ▼
mock «Фотопринт»: оператор переводит статусы (приём → модерация → производство → готов → завершён)
```

Сущности (рекомендованные ТЗ): user, product, project, order, order_status (`order_events`), interaction (`tickets`), metric (`events`), alert (предупреждения проверки).

## Решения и альтернативы
| Решение | Почему | Альтернатива | Когда пересмотреть |
|---|---|---|---|
| Монолит FastAPI + SQLite | за 3 дня быстрее всего, ноль инфраструктуры | микросервисы, Postgres | пилот: нагрузка, параллельная запись |
| Фронтенд без сборки | мгновенный старт, нет npm-проблем | React/Vite | если команда фронтенда вырастет |
| Telegram Mini App + обычный веб | жюри откроет по ссылке; потом PWA/Expo | нативное приложение | после пилота, для сторов |
| Проверка качества алгоритмами (Pillow) | детерминированно, офлайн, проверяемо | vision-LLM | нужны смысловые оценки (закрытые глаза) |
| Автомакет-алгоритм | предсказуем, мгновенный | LLM-раскладка по теме | тематические альбомы |
| Данные на сервере, а не в localStorage | черновик переживает смену устройства | локально | офлайн-очередь загрузки |
| Leaflet + OSM | без ключей и регистрации | Яндекс.Карты + геокодер | нужны реальные ПВЗ и адреса |

## API (кратко)
`POST /api/auth/{login|guest|telegram}`, `GET /api/products`, `POST /api/price`, `POST /api/projects`, `PATCH /api/projects/{id}`,
`POST /api/projects/{id}/photos`, `POST …/automaket`, `GET …/check`, `POST /api/orders` (+`Idempotency-Key`), `GET /api/orders/{id}`,
`POST /api/orders/{id}/tickets`, `DELETE /api/me/data`, `GET /api/admin/{orders|audit|funnel|tickets}`, `POST /api/admin/orders/{id}/status`.
Документация Swagger доступна на `/docs` при запущенном сервере.
