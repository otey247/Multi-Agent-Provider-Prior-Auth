# =============================================================================
# Backend: Python + Claude Agent SDK (CLI bundled in platform wheel)
# =============================================================================
# Base image pulled from the ECR Public mirror of Docker Official Images to avoid
# Docker Hub's anonymous pull rate limit ("toomanyrequests") on the ACR build
# agent. Override with --build-arg BASE_REGISTRY=docker.io/library for Docker Hub.
ARG BASE_REGISTRY=public.ecr.aws/docker/library
FROM ${BASE_REGISTRY}/python:3.12-slim

# Install curl for health check
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Copy standards-aligned policy packs (CMS-0057 / Da Vinci — PRD Component F).
# Bundled here so they ship in the image; the ./backend build context cannot
# reach a repo-root directory. Loader resolves <backend>/policy_packs.
COPY policy_packs/ ./policy_packs/

# Non-root user for security
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

# Runtime configuration
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
