FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 PORT=8080

COPY web-gateway/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY web-gateway/app.py ./app.py

EXPOSE 8080
CMD ["sh","-c","uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
