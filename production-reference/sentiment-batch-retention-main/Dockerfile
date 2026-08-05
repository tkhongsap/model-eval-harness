# Use python:3.11.9-slim image
FROM python:3.11.9-slim

# Set working directory inside container
WORKDIR /app

# Copy requirements file first for better caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source code
COPY . .

# Run the app
CMD ["python", "-m", "src.main"]