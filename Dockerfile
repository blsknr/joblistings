FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    JOBLISTINGS_DATA_DIR=/app/data \
    JOBLISTINGS_HOST=0.0.0.0 \
    HOME=/tmp

WORKDIR /app
COPY requirements.txt ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 ca-certificates \
    && pip install --no-cache-dir --require-hashes -r requirements.txt \
    && python -m playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 joblistings \
    && useradd --uid 10001 --gid 10001 --no-create-home joblistings \
    && mkdir /app/data \
    && chown joblistings:joblistings /app/data

COPY app.py tracker.py importer.py profiles.py taxonomy.py ./
COPY static/ ./static/
COPY tests/ ./tests/

USER joblistings
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8765/api/health', timeout=2)"
STOPSIGNAL SIGTERM
CMD ["python", "app.py"]
