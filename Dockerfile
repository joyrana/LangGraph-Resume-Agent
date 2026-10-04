# Ubuntu 24.04 ships LibreOffice 24.2.x, the series the visual-diff thresholds were calibrated on.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# Renderer, rasteriser and a documented font set (substitutes for Calibri/Cambria/Arial/Times).
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv ca-certificates \
        libreoffice-writer-nogui poppler-utils \
        fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation2 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project --python /usr/bin/python3.12
COPY app ./app
COPY main.py ./

RUN useradd --create-home --uid 10001 resume && mkdir -p /data && chown resume /data \
    && soffice --version | tee /app/RENDERER_VERSION
USER resume

ENV RESUME_STORAGE_DIR=/data \
    RESUME_RENDERER_EXPECTED_VERSION=24.2
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
