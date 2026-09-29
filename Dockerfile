FROM python:3.9-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SADTALKER_DIR=/opt/SadTalker \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ffmpeg wget libgl1 libglib2.0-0 libsndfile1 build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir torch==1.13.1+cpu torchvision==0.14.1+cpu torchaudio==0.13.1+cpu \
    --extra-index-url https://download.pytorch.org/whl/cpu

RUN git clone --depth 1 https://github.com/OpenTalker/SadTalker.git /opt/SadTalker
WORKDIR /opt/SadTalker
RUN pip install --no-cache-dir -r requirements.txt
RUN bash scripts/download_models.sh

WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.116.1 uvicorn[standard]==0.35.0 httpx==0.28.1 pydantic==2.11.7
COPY avatar-service/app.py ./app.py

EXPOSE 8080
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
