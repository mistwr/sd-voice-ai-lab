# SD Voice AI Lab — Voice Worker
# Container persistente (NÃO correr como função serverless de curta duração).
# Deploy recomendado: Railway (mesmo padrão do Lumin AI) ou qualquer host
# que mantenha um processo Python vivo continuamente.

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pré-descarrega modelos locais (VAD, turn-detector) para arranque mais rápido
RUN python agent.py download-files || true

# "start" corre o worker em modo produção — liga-se ao LiveKit e fica
# à espera de dispatches, sem expor porta HTTP nenhuma.
CMD ["python", "agent.py", "start"]
