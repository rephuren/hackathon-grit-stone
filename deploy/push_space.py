"""Заливает приложение на Hugging Face Spaces одной командой (после `hf auth login`):
    cd app && .venv/bin/python ../deploy/push_space.py picpac
Создаёт Space (Docker), загружает папку deploy/hf-space, добавляет секреты из app/.env
(TELEGRAM_BOT_TOKEN, OPERATOR_PASSWORD) и случайный APP_SECRET. Токены никуда, кроме вашего Space, не отправляются."""
import os
import secrets
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
name = (sys.argv[1] if len(sys.argv) > 1 else "picpac").strip().lower()
subprocess.run(["sh", os.path.join(ROOT, "deploy", "make_space.sh")], check=True)

try:
    from huggingface_hub import HfApi
except ImportError:
    sys.exit("Сначала: .venv/bin/pip install huggingface_hub && .venv/bin/hf auth login")

env = {}
path = os.path.join(ROOT, "app", ".env")
if os.path.exists(path):
    for line in open(path, encoding="utf-8"):
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k] = v

api = HfApi()
user = api.whoami()["name"]
repo = f"{user}/{name}"
url = f"https://{user}-{name}.hf.space".lower()
api.create_repo(repo, repo_type="space", space_sdk="docker", exist_ok=True)
api.upload_folder(folder_path=os.path.join(ROOT, "deploy", "hf-space"), repo_id=repo, repo_type="space",
                  commit_message="picPac: загрузка")
secrets_to_set = {"APP_SECRET": secrets.token_hex(32), "WEBAPP_URL": url}
for key in ("TELEGRAM_BOT_TOKEN", "OPERATOR_PASSWORD"):
    if env.get(key):
        secrets_to_set[key] = env[key]
for key, value in secrets_to_set.items():
    api.add_space_secret(repo, key, value)
api.restart_space(repo)
print(f"\nГотово. Space: https://huggingface.co/spaces/{repo}\nАдрес приложения (после сборки, 5-10 минут): {url}")
print("Остановите локальный tma_start.py (Ctrl+C): бота может опрашивать только один сервер.")
