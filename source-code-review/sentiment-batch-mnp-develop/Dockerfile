# Use python:3.11.9-slim image
FROM python:3.11.9-slim

# Prevent Python from writing pyc files to disc
ENV PYTHONDONTWRITEBYTECODE=1
# Prevent Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED=1

# Set working directory inside container
WORKDIR /app

# Add /app to PYTHONPATH to allow imports from src
ENV PYTHONPATH="/app"

# Create a non-root user
RUN useradd -m appuser

# Copy requirements file first for better caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source code with ownership
COPY --chown=appuser . .

# Switch to non-root user
USER appuser

# Run the app
CMD ["python", "-m", "src.main"]