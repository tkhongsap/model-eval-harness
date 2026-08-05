# ─── Stage 1: Install dependencies ────────────────────────────────────────────
FROM python:3.11.14-slim-bookworm AS builder

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy dependency files first for better layer caching
# This layer is only rebuilt when pyproject.toml or uv.lock changes
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# ─── Stage 2: Runtime image ────────────────────────────────────────────────────
FROM python:3.11.14-slim-bookworm

# Prevent Python from writing pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app" \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Apply outstanding Debian security updates and refresh the bundled Python
# build tools (pip/setuptools/wheel) to clear base-image CVEs flagged by scans.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Create a non-root user
RUN useradd -m appuser

# Copy the pre-built virtual environment from builder stage
COPY --from=builder /app/.venv /app/.venv

# Copy only application source (excludes terraform, tests, cloud_build, etc.)
COPY --chown=root:root main.py ./
COPY --chown=root:root src/ ./src/
COPY --chown=root:root tasks/ ./tasks/
COPY --chown=root:root config/ ./config/
COPY --chown=root:root resources/ ./resources/

# Harden: app files owned by root, read/execute-only for the runtime user
RUN chmod -R u=rwX,go=rX /app/main.py /app/src /app/tasks /app/config /app/resources

# Switch to non-root user
USER appuser

# Default config path — overridden by Cloud Run job args at runtime
# e.g.: python main.py --config_path config/sentiment_telesale/telesale_pipeline_tasks.yml
ENTRYPOINT ["python", "main.py"]
CMD ["--config_path", ""]
