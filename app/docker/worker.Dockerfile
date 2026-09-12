# The worker image. Larger than the API's, because Docling brings layout and
# OCR models along with it.
#
# Both workers run from this one image; which one starts is decided by the
# command, not by the build. Two images that differ only in an entrypoint would
# be two things to build, push and keep in step.
#
#   docker build -f docker/worker.Dockerfile -t edgentrag/worker .
#   docker run edgentrag/worker python -m backend.workers.ingest
#   docker run edgentrag/worker python -m backend.workers.chat

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Docling caches its models here. Mount a volume at this path to avoid
    # re-downloading them every time a container starts.
    HF_HOME=/home/app/.cache/huggingface

WORKDIR /app

# Docling needs a few native libraries that the slim image does not carry.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements-worker.txt /app/backend/
RUN pip install -r backend/requirements-worker.txt

COPY backend /app/backend

# /data and the model cache are both created here so their named volumes
# inherit this ownership. A volume mounted over a path that does not exist in
# the image arrives owned by root, and a process running as uid 10001 then
# cannot write to it -- which for /data means SQLite fails at start-up.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /home/app/.cache/huggingface /data \
    && chown -R app:app /app /home/app /data
USER app

# A worker has no port and nothing to poll, so liveness is "is the process
# still there" — which is what `restart: unless-stopped` already gives us. A
# worker that hangs shows up as a queue that stops draining, which is the
# honest signal: it measures whether work is moving, not whether a process
# exists. Watch it with `make queues`.

# Overridden in docker-compose.yml, which runs this image twice with different
# commands. Ingest is the default because it is the one that matters most.
CMD ["python", "-m", "backend.workers.ingest"]
