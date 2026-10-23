# syntax=docker/dockerfile:1
FROM python:3.12-slim

# FORWARDED_ALLOW_IPS : proxys dont les en-têtes X-Forwarded-* sont crus (IP réelle
# pour l'anti brute-force). « * » seulement si le port n'est joignable que par le
# reverse proxy (cas de Caddy, réglé par install.sh --domain).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TICOSHOP_DATA_DIR=/data \
    TICOSHOP_BACKUP_DIR=/backups \
    FORWARDED_ALLOW_IPS=127.0.0.1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

RUN useradd --system --uid 1000 ticoshop && mkdir -p /data /backups && chown ticoshop /data /backups
USER ticoshop
VOLUME ["/data", "/backups"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
