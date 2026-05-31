FROM python:3.10-slim

# Set environment variables for non-interactive installs, unbuffered logging,
# and to prevent python writing pyc files to disk.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TORCH_NUM_THREADS=4 \
    PORT=7860

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files (excluding those in .dockerignore / .gitignore)
COPY . .

# Ensure data directory exists and SQLite file is writable
RUN mkdir -p data && chmod -R 777 data

# Expose port (default for HF Spaces is 7860, routed automatically)
EXPOSE 7860

# Launch server
CMD ["python", "app.py"]
