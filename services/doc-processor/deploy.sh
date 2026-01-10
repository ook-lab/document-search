#!/bin/bash
  set -e
  PROJECT_ID="consummate-yew-479020-u2"
  SERVICE_NAME="doc-processor"
  REGION="asia-northeast1"
  cd ~/document-management-system
  docker build -t asia-northeast1-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:latest -f services/doc-processor/Dockerfile .
  docker push asia-northeast1-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:latest
  gcloud run deploy ${SERVICE_NAME} --image asia-northeast1-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:latest --region ${REGION} --memory 16Gi --cpu 4 --timeout 3600 --allow-unauthenticated --min-instances 0 --max-instances 10 --concurrency 1 --execution-environment gen1
  echo "✅ ${SERVICE_NAME} deployed!"
