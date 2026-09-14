# syntax=docker/dockerfile:1
FROM python:3.11-slim AS builder

RUN pip install --no-cache-dir uv==0.11.*

WORKDIR /build
COPY shared/common shared/common
COPY shared/contracts shared/contracts
COPY central/api central/api

RUN uv venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

RUN uv pip install --no-cache \
    ./shared/common \
    ./shared/contracts \
    ./central/api

FROM python:3.11-slim AS runtime

RUN useradd --system --create-home --home-dir /home/fleetview \
    --shell /usr/sbin/nologin fleetview

COPY --from=builder /opt/venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY central/api/alembic.ini /app/alembic.ini
COPY central/api/migrations /app/migrations

USER fleetview

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]

EXPOSE 8000

ARG VERSION=dev
ENV FLEETVIEW_VERSION=${VERSION}
LABEL org.opencontainers.image.version=${VERSION}

CMD ["fleetview-central"]
