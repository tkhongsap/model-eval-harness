# Use Python 3.11 slim image
FROM python:3.11-slim

# Upgrade all OS packages to get patched versions
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y libmagic-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the requirements file first to leverage Docker caching
COPY app/requirements.txt .

# Upgrade pip and wheel to patched versions, then install dependencies
RUN pip install --upgrade pip==26.0.1 wheel==0.46.2
RUN pip install --no-cache-dir -r requirements.txt

# Copy application and config
COPY app/ ./app
COPY config/ ./config/

# Expose port 8080 for the application
EXPOSE 8080

RUN adduser --disabled-password --gecos "" appuser
USER appuser

CMD ["python", "-m", "app.main"]