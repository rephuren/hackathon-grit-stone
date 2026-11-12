FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH DATA_DIR=/home/user/data PYTHONUNBUFFERED=1
WORKDIR /home/user/app
COPY --chown=user app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt onnxruntime numpy
COPY --chown=user app/ .
RUN mkdir -p /home/user/data/models \
 && curl -L -sS -f -o /home/user/data/models/realesr-general-x4v3.onnx https://huggingface.co/CoderViking/realesr-general-x4v3-onnx/resolve/main/realesr-general-x4v3.onnx \
 && curl -L -sS -f -o /home/user/data/models/u2netp.onnx https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx \
 && curl -L -sS -f -o /home/user/data/models/lama_fp32.onnx https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx
EXPOSE 7860
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "7860"]
