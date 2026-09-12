# The API image. Small on purpose — no Docling, no models, nothing heavy.
#
# Build from the v2 directory so `backend/` is in context:
#   docker build -f docker/api.Dockerfile -t edgentrag/api .

FROM python:3.12-slim AS base

# Do not write .pyc, do not buffer stdout (so logs appear in `docker logs`
# immediately rather than when a buffer fills), and fail fast on pip.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, as their own layer: this is the expensive step and it is
# only redone when the requirements change, not on every code edit.
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend /app/backend

# Bundled so the image can create the queues on its own:
#   docker compose run --rm api python -m scripts.bootstrap
COPY scripts /app/scripts

# Never run as root. If the container is compromised, this is the difference
# between an inconvenience and a bad day.
#
# /data is created here, owned by `app`, and that is not cosmetic: Docker
# initialises an empty named volume from the image's directory at that path,
# ownership included. Without this the volume arrives owned by root, the
# process runs as uid 10001, and SQLite fails to create the file at start-up.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data \
    && chown -R app:app /app /data
USER app

EXPOSE 8000

# Liveness only — deliberately no database or Redis check here. If the health
# check tested them, a brief Redis blip would mark the container unhealthy and
# restart it, turning a recoverable problem into a longer outage. /ready is the
# endpoint that reports dependencies, and it is for humans to read.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"

# One uvicorn worker per container. Scale by running more containers, not more
# workers inside one — a crash then costs one container rather than all of them,
# and the containers can later move to separate machines unchanged.
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
