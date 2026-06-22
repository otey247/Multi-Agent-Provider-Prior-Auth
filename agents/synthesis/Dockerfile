# =============================================================================
# Synthesis Decision Agent — MAF Hosted Agent container
# =============================================================================
# Built with --platform linux/amd64 via 'az acr build --platform linux/amd64'
# (Foundry Hosted Agents run on Linux AMD64 infrastructure)
# See: https://aka.ms/foundry-hosted-agents
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

# Copy agent code and skills
COPY main.py ./
COPY schemas.py ./
COPY skills/ ./skills/

# Non-root user
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1

EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8088/readiness || exit 1

CMD ["python", "main.py"]
