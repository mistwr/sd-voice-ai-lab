# LUMIN Chatterbox runtime
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 git \
    && rm -rf /var/lib/apt/lists/*

COPY chatterbox-service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY chatterbox-service/app.py ./app.py

EXPOSE 8080
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
