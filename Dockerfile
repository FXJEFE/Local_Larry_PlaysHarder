# ============================================================================
# LARRY G-FORCE agent image (src/ layout: main.py, subagents/, tools/, utils/)
#
# Build:        docker compose build          (from this src/ directory)
# Run status:   docker compose run --rm agent python main.py status
# Everything:   docker compose up -d          (ollama + agent/telegram bot)
#
# Ollama runs in its own container (see docker-compose.yml); this image only
# contains the Python agent. OLLAMA_HOST is injected by compose.
# ============================================================================

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg: audio decode for transcription; curl: healthchecks/debugging
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first so code changes don't bust the pip cache
COPY requirements-docker.txt .
RUN pip install -r requirements-docker.txt

COPY . .

# Runtime state lives in volumes (declared in compose): /app/memory, /app/logs,
# /app/sandbox, /app/exports
RUN mkdir -p memory logs sandbox exports

# Inside the network the Ollama container is reachable as "ollama"
ENV OLLAMA_HOST=http://ollama:11434 \
    OLLAMA_NUM_PARALLEL=1

CMD ["python", "main.py", "all"]
