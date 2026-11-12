# Нативные приложения picPac (iOS, Android)

Идея: весь интерфейс уже написан на обычном вебе (`app/frontend`, без сборки), поэтому нативные приложения —
это «оболочка» Capacitor вокруг того же кода. Экраны не переписываются, обновления интерфейса попадают во все платформы сразу.

## Что нужно один раз
- Node.js 20+, для iOS: Xcode и CocoaPods, для Android: Android Studio.
- Сервер с постоянным HTTPS-адресом (см. `docs/07-portability.md`) и в его `.env`:
  `CORS_ORIGINS=capacitor://localhost,http://localhost,https://localhost,ionic://localhost`,
  `PUBLIC_BASE_URL=https://ваш-домен`.

## Сборка
```bash
cd mobile
npm install
SERVER=https://ваш-домен sh sync.sh     # копирует веб-код и прописывает адрес сервера
npx cap add ios      # один раз
npx cap add android  # один раз
npx cap sync
npx cap open ios     # откроется Xcode: запуск на симуляторе или устройстве
npx cap open android # откроется Android Studio
```
После правок интерфейса: `SERVER=... sh sync.sh && npx cap sync`.

## Не проверено
Эти оболочки в рамках хакатона не собирались и не запускались (нет Xcode/Android Studio в окружении). Конфиг типовой для Capacitor 7,
но первые запуски почти наверняка потребуют мелких правок. Для App Store нужен вход через Apple, если есть другой вход через аккаунты.
Для macOS и Windows подойдёт Tauri или Electron с той же папкой `www`.
