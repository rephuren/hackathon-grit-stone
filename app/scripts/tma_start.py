"""Запуск picPac как Telegram Mini App одной командой:  .venv/bin/python scripts/tma_start.py
1) поднимает приложение на порту 8010; 2) открывает бесплатный туннель ngrok и берёт из него HTTPS-адрес;
3) настраивает бота (кнопка меню, команды), если в app/.env есть TELEGRAM_BOT_TOKEN. Остановка: Ctrl+C.
Туннель по умолчанию cloudflared (brew install cloudflared; без аккаунта и без страницы-предупреждения).
Другие туннели: TMA_TUNNEL=tailscale или zrok. Свой туннель: TMA_URL=https://ваш-адрес (скрипт только запустит приложение и настроит бота). Запасной вариант: TMA_TUNNEL=ngrok (нужна авторизация: ngrok config add-authtoken ...). Токен берётся от @BotFather."""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, APP)
PORT = int(os.environ.get("TMA_PORT", "8010"))


TS_DIR = os.path.expanduser("~/.tailscale")
TS_SOCK = os.path.join(TS_DIR, "tailscaled.sock")


def ts(*args, timeout=60):
    return subprocess.run(["tailscale", f"--socket={TS_SOCK}", *args], capture_output=True, text=True, timeout=timeout)


def ts_start():
    """Поднимает демон Tailscale без прав администратора (userspace), если он не запущен, и включает Funnel на нашем порту."""
    alive = False
    if os.path.exists(TS_SOCK):
        try:
            alive = ts("status", timeout=10).returncode == 0
        except Exception:
            alive = False
    if not alive:
        os.makedirs(os.path.join(TS_DIR, "var"), exist_ok=True)
        subprocess.Popen(["tailscaled", "--tun=userspace-networking", f"--state={TS_DIR}/tailscaled.state",
                          f"--statedir={TS_DIR}/var", f"--socket={TS_SOCK}"], stdout=open(os.path.join(TS_DIR, "tailscaled.log"), "a"),
                         stderr=subprocess.STDOUT)
        time.sleep(6)
    proc = subprocess.Popen(["tailscale", f"--socket={TS_SOCK}", "funnel", "--bg", str(PORT)], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    try:
        out = proc.communicate(timeout=40)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        out = ""
    m = re.search(r"https://[^\s/]+\.ts\.net", out)
    if not m:
        print("Funnel не запустился. Вывод:", out.strip()[:300])
    return None, (m.group(0) if m else None)


def tunnel_url(timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2) as r:
                for t in json.load(r)["tunnels"]:
                    if t["public_url"].startswith("https://"):
                        return t["public_url"]
        except Exception:
            pass
        time.sleep(1)
    return None


def start_tunnel():
    """Возвращает (процесс, https-адрес)."""
    if os.environ.get("TMA_URL"):  # туннель запущен вами отдельно (localhost.run, tailscale, zrok, tuna и т.п.)
        return None, os.environ["TMA_URL"].rstrip("/")
    kind = os.environ.get("TMA_TUNNEL") or ("tailscale" if os.path.exists(TS_DIR) else "cloudflared")
    if kind == "tailscale":  # постоянный адрес *.ts.net без страницы-предупреждения; нужен аккаунт Tailscale и включённый Funnel
        return ts_start()
    if kind == "zrok":  # бесплатный публичный адрес *.share.zrok.io; нужен аккаунт zrok (zrok enable)
        proc = subprocess.Popen(["zrok", "share", "public", "--headless", f"localhost:{PORT}"], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        end = time.time() + 40
        while time.time() < end and proc.poll() is None:
            m = re.search(r"https://[a-z0-9]+\.share\.zrok\.io", proc.stdout.readline())
            if m:
                threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()
                return proc, m.group(0)
        return proc, None
    if kind == "ngrok" or not shutil.which("cloudflared"):
        proc = subprocess.Popen(["ngrok", "http", str(PORT), "--log", "stdout"], stdout=open(os.path.join(APP, "data", "ngrok.log"), "w"), stderr=subprocess.STDOUT)
        return proc, tunnel_url()
    proc = subprocess.Popen(["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{PORT}", "--no-autoupdate"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    end = time.time() + 40
    while time.time() < end and proc.poll() is None:
        line = proc.stderr.readline()
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            threading.Thread(target=lambda: [None for _ in proc.stderr], daemon=True).start()  # не даём забиться каналу
            return proc, m.group(0)
    return proc, None


ngrok = None
server = None
try:
    ngrok, url = start_tunnel()
    if not url:
        sys.exit("Не удалось получить адрес туннеля. Проверьте интернет (для ngrok: ngrok config add-authtoken ...).")
    env = {**os.environ, "WEBAPP_URL": url}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(PORT)], cwd=APP, env=env)
    os.environ["WEBAPP_URL"] = url
    from backend import tgbot  # noqa: E402
    print(f"\nПубличный адрес Mini App: {url}")
    if tgbot.enabled():
        print("Настройка бота:", json.dumps(tgbot.setup(), ensure_ascii=False))
        print("Откройте своего бота в Telegram и нажмите «Menu» или отправьте /start.")
    else:
        print("TELEGRAM_BOT_TOKEN в app/.env пуст: приложение доступно по адресу выше, но бот не настроен.")
    print("Адрес меняется при каждом запуске (бесплатный туннель). Остановка: Ctrl+C.\n")
    server.wait()
except KeyboardInterrupt:
    pass
finally:
    if not os.environ.get("TMA_URL") and (os.environ.get("TMA_TUNNEL") == "tailscale" or (not os.environ.get("TMA_TUNNEL") and os.path.exists(TS_DIR))):
        ts("funnel", "reset")
    for p in (server, ngrok):
        if p and p.poll() is None:
            p.send_signal(signal.SIGTERM)
