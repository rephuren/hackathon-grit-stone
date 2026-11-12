"""Скачивает модели (нужен интернет; пакеты: pip install onnxruntime numpy, для генерации ещё torch diffusers transformers
accelerate safetensors sentencepiece sacremoses).
  python scripts/fetch_models.py           # небольшие модели (upscale, bgremove, inpaint)
  python scripts/fetch_models.py upscale   # только улучшение качества (5 МБ)
  python scripts/fetch_models.py bgremove  # удаление фона (5 МБ)
  python scripts/fetch_models.py inpaint   # удаление объектов (208 МБ)
  python scripts/fetch_models.py imagegen  # генерация кадра по описанию + перевод ru→en (≈5,5 ГБ, явно по запросу)
Без моделей приложение работает, соответствующие кнопки скрыты (генерация уходит на запасной внешний сервис).
Лицензии: Real-ESRGAN (realesr-general-x4v3) — BSD-3-Clause; U²-Net (u2netp) — Apache-2.0; LaMa — Apache-2.0;
LCM Dreamshaper v7 — MIT; opus-mt-ru-en — CC-BY-4.0."""
import os
import subprocess
import sys
import urllib.request

MODELS = {
    "upscale": ("realesr-general-x4v3.onnx", "https://huggingface.co/CoderViking/realesr-general-x4v3-onnx/resolve/main/realesr-general-x4v3.onnx"),
    "bgremove": ("u2netp.onnx", "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx"),
    "inpaint": ("lama_fp32.onnx", "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx"),
}
root = os.environ.get("DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
os.makedirs(os.path.join(root, "models"), exist_ok=True)


# только веса PyTorch: в репозитории модели лежат ещё ONNX/OpenVINO-копии, они не нужны
GEN_FILES = ["model_index.json", "scheduler/*.json", "tokenizer/*", "feature_extractor/*.json", "*/config.json",
             "unet/diffusion_pytorch_model.safetensors", "vae/diffusion_pytorch_model.safetensors",
             "text_encoder/model.safetensors", "safety_checker/model.safetensors"]


def fetch_imagegen():
    from huggingface_hub import snapshot_download

    base = os.path.join(root, "models")
    snapshot_download("Helsinki-NLP/opus-mt-ru-en", local_dir=os.path.join(base, "opus-mt-ru-en"),
                      allow_patterns=["*.json", "*.spm", "pytorch_model.bin"])
    snapshot_download("SimianLuo/LCM_Dreamshaper_v7", local_dir=os.path.join(base, "lcm-dreamshaper-v7"),
                      allow_patterns=GEN_FILES)
    print("Готово: модели генерации и перевода в", base)


for key in (sys.argv[1:] or MODELS):
    if key == "imagegen":
        fetch_imagegen()
        continue
    name, url = MODELS[key]
    dest = os.path.join(root, "models", name)
    if os.path.exists(dest) and os.path.getsize(dest) > 1_000_000:
        print("Уже скачано:", dest)
        continue
    print("Скачиваем", url)
    try:
        urllib.request.urlretrieve(url, dest + ".part")
    except Exception:  # на macOS у python часто нет корневых сертификатов: пробуем curl
        subprocess.run(["curl", "-L", "-sS", "-f", "-o", dest + ".part", url], check=True)
    os.replace(dest + ".part", dest)
    print("Готово:", dest, os.path.getsize(dest) // 1024, "КБ")
