FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_ROOT_USER_ACTION=ignore
RUN apt-get update && apt-get install -y --no-install-recommends cron ca-certificates tzdata sqlite3 procps util-linux openssl tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 -s /bin/bash docker
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY . /app
RUN chmod 0644 /app/crontab && crontab -u docker /app/crontab \
    && mkdir -p /app/config && chown -R docker:docker /app
STOPSIGNAL SIGTERM
CMD ["/bin/bash", "/app/entrypoint.sh"]
