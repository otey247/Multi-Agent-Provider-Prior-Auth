# =============================================================================
# Medical-data MCP server — self-hosted replacement for the retired DeepSense
# MCP servers. Serves ICD-10, clinical-trials, NPI and CMS-coverage tools over
# MCP Streamable HTTP on per-domain paths.
# =============================================================================
# Build for Foundry/ACA (Linux AMD64):
#   az acr build --registry <acr> --image mcp-medical-data:<tag> \
#       --platform linux/amd64 ./mcp-servers/medical-data
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py ./
COPY demo_fixtures.py ./

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1 PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["python", "server.py"]
