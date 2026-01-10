#!/bin/bash
  set -e
  PROJECT_ID="consummate-yew-479020-u2"
  SERVICE_NAME="mail-doc-search-system"
  REGION="asia-northeast1"
  cd ~/document-management-system
  docker build -t asia-northeast1-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:latest -f services/doc-search/Dockerfile .
  docker push asia-northeast1-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:latest
  gcloud run deploy ${SERVICE_NAME} --image asia-northeast1-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:latest --region ${REGION} --memory 4Gi --cpu 2 --timeout 300 --allow-unauthenticated
  echo "✅ ${SERVICE_NAME} deployed!"
