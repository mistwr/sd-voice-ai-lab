FROM python:3.10-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENVOICE_DIR=/opt/OpenVoice \
    OPENVOICE_CONVERTER_DIR=/opt/OpenVoice/checkpoints_v2/converter \
    PIPER_MODEL=/app/voices/lumin-ptpt.onnx \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ffmpeg wget unzip libsndfile1 build-essential espeak-ng \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu torch==2.2.2+cpu torchaudio==2.2.2+cpu
RUN git clone --depth 1 https://github.com/myshell-ai/OpenVoice.git /opt/OpenVoice \
    && pip install --no-cache-dir -e /opt/OpenVoice --no-deps \
    && pip install --no-cache-dir \
       "numpy==1.23.5" "librosa==0.9.2" "soundfile==0.12.1" \
       "eng_to_ipa==0.0.2" "inflect==7.0.0" "Unidecode==1.3.7" \
       "pypinyin==0.50.0" "cn2an==0.5.22" "jieba==0.42.1" "langid==1.1.6"

RUN wget -q https://myshell-public-repo-host.s3.amazonaws.com/openvoice/checkpoints_v2_0417.zip -O /tmp/openvoice.zip \
    && unzip -q /tmp/openvoice.zip -d /opt/OpenVoice \
    && rm /tmp/openvoice.zip

RUN pip install --no-cache-dir piper-tts==1.8.0 flask==3.1.2 gunicorn==23.0.0 soundfile==0.12.1

RUN mkdir -p /app/voices \
    && python -c "import urllib.request; urllib.request.urlretrieve('https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_PT/tug%C3%A3o/medium/pt_PT-tug%C3%A3o-medium.onnx?download=true','/app/voices/lumin-ptpt.onnx'); urllib.request.urlretrieve('https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_PT/tug%C3%A3o/medium/pt_PT-tug%C3%A3o-medium.onnx.json?download=true','/app/voices/lumin-ptpt.onnx.json')"

COPY openvoice-service/app.py ./app.py

EXPOSE 8080
CMD ["sh","-c","gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 2 --timeout 180 app:app"]
