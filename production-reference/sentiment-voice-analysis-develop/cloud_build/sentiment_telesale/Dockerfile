# ─── Stage 1: Install dependencies ────────────────────────────────────────────
FROM python:3.11.9-slim AS builder

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy dependency files first for better layer caching
# This layer is only rebuilt when pyproject.toml or uv.lock changes
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-build --no-install-project

# ─── Stage 2: Runtime image ────────────────────────────────────────────────────
FROM python:3.11.9-slim

# Prevent Python from writing pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app" \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

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

# Switch to non-root user
USER appuser

# Default config path — overridden by Cloud Run job args at runtime
# e.g.: python main.py --config_path config/sentiment_telesale/telesale_pipeline_tasks.yml
ENTRYPOINT ["python", "main.py"]
CMD ["--config_path", ""]