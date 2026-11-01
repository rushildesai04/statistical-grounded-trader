# SGT cloud runner (2026-10-30): scheduled paper jobs on Fly.io. No inbound ports.
FROM python:3.12.12-slim-bookworm

ARG SUPERCRONIC_VERSION=v0.2.49
ARG SUPERCRONIC_SHA256=a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1

# libgomp1: LightGBM runtime. tzdata: exchange-calendar / cron in America/New_York.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 tzdata curl ca-certificates \
    && curl -fsSL -o /usr/local/bin/supercronic \
       "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
    && echo "${SUPERCRONIC_SHA256}  /usr/local/bin/supercronic" | sha256sum -c - \
    && chmod +x /usr/local/bin/supercronic \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 sgt
WORKDIR /app
COPY deploy/fly/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock
COPY src /app/src
COPY scripts /app/scripts
COPY deploy/fly/crontab deploy/fly/entrypoint.sh /app/deploy/
RUN chmod +x /app/deploy/entrypoint.sh

ENV TZ=America/New_York \
    PYTHONPATH=/app/src:/app/scripts \
    PYTHONUNBUFFERED=1 \
    PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
    SGT_HOME=/data/sgt

# Starts as root only to own the volume mount, then drops to uid 10001 (entrypoint.sh).
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
