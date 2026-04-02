#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-foundations-bot}"
CONTAINER_NAME="${CONTAINER_NAME:-foundations-bot}"
DATA_DIR="${DATA_DIR:-$(pwd)/data}"
ENV_FILE="${ENV_FILE:-.env}"

mkdir -p "${DATA_DIR}"

docker build -t "${IMAGE_NAME}" .
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  --env-file "${ENV_FILE}" \
  -e SQLITE_PATH=/data/foundations_bot.db \
  -p 8080:8080 \
  -v "${DATA_DIR}:/data" \
  "${IMAGE_NAME}"

echo "Deployed ${CONTAINER_NAME} with SQLite at ${DATA_DIR}/foundations_bot.db"
