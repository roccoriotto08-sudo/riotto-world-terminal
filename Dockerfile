# Riotto World Terminal - production image (public deployment)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PUBLIC_MODE=true \
    HOST=0.0.0.0 \
    PORT=8700 \
    DB_PATH=/data/macro.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend backend
COPY config config
COPY frontend frontend

# /data should be a persistent volume on the host (Railway volume / Render disk) so history survives
# redeploys. Runs as root because hosted volumes are mounted root-owned.
RUN mkdir -p /data
EXPOSE 8700

# ONE worker on purpose: the data scheduler runs inside the app process; more workers would
# poll every provider several times over and split the live push stream.
CMD ["sh", "-c", "exec uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8700} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
