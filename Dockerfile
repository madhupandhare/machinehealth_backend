# ─────────────────────────────────────────────────────────────────────────────
# Dockerfile
# Runs both the Fog Node and the Flask Dashboard in one container.
# A supervisor-style entrypoint starts both processes.
#
# Build:
#   docker build -t imhm:latest .
#
# Run locally:
#   docker run --env-file .env \
#     -v $(pwd)/certs:/app/certs:ro \
#     -p 5000:5000 imhm:latest
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

LABEL maintainer="imhm-project"
LABEL description="Industrial Machine Health Monitor — Fog Node + Dashboard"

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create certs directory (real certs are mounted at runtime)
RUN mkdir -p /app/certs

# Supervisor configuration — runs fog_node.py + gunicorn in parallel
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose Flask dashboard port
EXPOSE 5000

# Health check — hits the API to confirm Flask is alive
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD curl -f http://localhost:5000/api/machines || exit 1

# Entrypoint: supervisord manages both processes
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
