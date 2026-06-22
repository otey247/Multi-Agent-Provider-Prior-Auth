# =============================================================================
# Frontend: Build Next.js app, serve with Nginx
# =============================================================================

# Base images are pulled from the ECR Public mirror of Docker Official Images to
# avoid Docker Hub's anonymous pull rate limit ("toomanyrequests") on the ACR
# build agent. Override with --build-arg BASE_REGISTRY=docker.io/library to pull
# from Docker Hub instead.
ARG BASE_REGISTRY=public.ecr.aws/docker/library

# Stage 1 — Build
FROM ${BASE_REGISTRY}/node:20-slim AS builder

WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# Stage 2 — Serve
FROM ${BASE_REGISTRY}/nginx:alpine

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy custom nginx config as template (uses ${BACKEND_URL})
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Default backend URL for Docker Compose; override via env var for Container Apps
ENV BACKEND_URL=http://backend:8000

# Copy built frontend (Next.js static export produces out/)
COPY --from=builder /app/out /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://localhost:80/ || exit 1

# nginx:alpine entrypoint automatically runs envsubst on /etc/nginx/templates/*.template
CMD ["nginx", "-g", "daemon off;"]
