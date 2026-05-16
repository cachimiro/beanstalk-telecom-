# Build context is the project root (set in docker-compose.yml)
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY worker/requirements.txt ./worker/requirements.txt
RUN pip install --no-cache-dir -r worker/requirements.txt

# Copy both api (shared services) and worker code
COPY api/ ./api/
COPY worker/ ./worker/

CMD ["python", "worker/worker.py"]
