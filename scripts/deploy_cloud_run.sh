#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID first.}"
REGION="${REGION:?Set REGION first.}"
SERVICE_NAME="${SERVICE_NAME:-foundations-bot}"
REPOSITORY="${REPOSITORY:-foundations-bot}"
IMAGE_NAME="${IMAGE_NAME:-foundations-bot}"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d-%H%M%S)}"
ENV_FILE="${ENV_FILE:-deploy/cloudrun.env.yaml}"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:${IMAGE_TAG}"

gcloud services enable \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  sqladmin.googleapis.com \
  --project="${PROJECT_ID}"

if ! gcloud artifacts repositories describe "${REPOSITORY}" \
  --location="${REGION}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${REPOSITORY}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Docker images for Foundations Bot" \
    --project="${PROJECT_ID}"
fi

gcloud builds submit \
  --tag "${IMAGE_URI}" \
  --project="${PROJECT_ID}" \
  .

deploy_args=(
  run deploy "${SERVICE_NAME}"
  --project="${PROJECT_ID}"
  --region="${REGION}"
  --platform=managed
  --image="${IMAGE_URI}"
  --port=8080
  --min-instances=1
  --max-instances=1
  --concurrency=1
  --cpu=1
  --memory=1Gi
  --no-allow-unauthenticated
)

if [[ -f "${ENV_FILE}" ]]; then
  deploy_args+=(--env-vars-file "${ENV_FILE}")
fi

if [[ -n "${INSTANCE_CONNECTION_NAME:-}" ]]; then
  deploy_args+=(--add-cloudsql-instances "${INSTANCE_CONNECTION_NAME}")
fi

gcloud "${deploy_args[@]}"

echo "Deployed ${SERVICE_NAME} with image ${IMAGE_URI}"
