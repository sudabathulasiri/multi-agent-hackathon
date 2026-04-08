# ── Stage 1: Build ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim

# Create a non-root user (Cloud Run best practice)
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY main.py .

# SQLite database lives in /tmp (ephemeral, writable on Cloud Run)
ENV DB_PATH=/tmp/librarian.db

# Cloud Run injects PORT; uvicorn reads it inside main.py
ENV PORT=8080

USER appuser

# Expose the default port for local testing
EXPOSE 8080

# Production command
CMD ["python", "main.py"]
