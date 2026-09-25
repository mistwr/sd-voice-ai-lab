FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Cache all model assets into the image during build.
RUN python -c "from tts_eu_pt import TTS; t=TTS(); w=t.say('Olá. Sou o Lumin.'); print('Kokoro PT-PT cached:', len(w))"

COPY app.py .

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
