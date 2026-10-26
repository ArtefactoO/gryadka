## Frontend build
FROM node:22-alpine AS frontend

LABEL version=gryadka:2.0.7

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/packages/project-ui/package.json ./packages/project-ui/package.json
RUN npm ci --ignore-scripts

COPY frontend ./
RUN npm run build

## Backend with embedded static
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

COPY backend/requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY backend/app ./app
COPY VERSION ./VERSION
COPY CHANGELOG.md ./CHANGELOG.md

COPY --from=frontend /app/dist ./frontend/dist

# Create non-root user and set permissions
RUN groupadd -r appgroup -g 1000 && \
    useradd -r -u 1000 -g appgroup -d /app -s /sbin/nologin appuser && \
    chown -R 1000:1000 /app

# /tmp is usually 777, but ensure it's writable
RUN chmod 1777 /tmp

EXPOSE 8082

USER 1000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8082"]
