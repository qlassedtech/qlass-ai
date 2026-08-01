# Build context is the REPO ROOT (not backend/), unlike backend/Dockerfile —
# scripts/*.py resolve their imports via
# `sys.path.insert(0, Path(__file__).resolve().parents[1] / "backend")`,
# i.e. they expect scripts/ and backend/ as siblings, exactly like a real
# checkout on the host. See docker-compose.ovh.yml's `cron` service.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY scripts /app/scripts

COPY docker/cron/crontab /etc/cron.d/qlass-cron
RUN chmod 0644 /etc/cron.d/qlass-cron && crontab /etc/cron.d/qlass-cron

COPY docker/cron/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
