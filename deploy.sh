#!/bin/bash
# AI Study Companion - MASTER Deployment Script

echo "🚀 Starte Master Deployment mit spezifischen Dockerfiles..."

# --- Konfiguration ---
PROJECT_ID="ai-study-companion-480112"
REGION="europe-west1"
SERVICE_ACCOUNT="study-companion-sa@$PROJECT_ID.iam.gserviceaccount.com"
DATA_STORE_ID="asc-knowledge-base_1769181814756"
DATA_STORE_LOCATION="eu"
GCS_BUCKET_NAME="ai-study-companion"

# --- 1. INDEXER SERVICE DEPLOYMENT ---
echo "--------------------------------------------"
echo "📦 Baue INDEXER SERVICE (Dockerfile.index)..."
INDEXER_IMAGE="gcr.io/$PROJECT_ID/file-indexer-service"

# Wir nutzen --config oder weisen Docker an, das spezifische File zu nutzen
# Da gcloud builds submit das lokale Verzeichnis packt:
gcloud builds submit --tag $INDEXER_IMAGE --dockerfile indexer-service/Dockerfile.index indexer-service/

gcloud run deploy file-indexer-service-9404 \
  --image $INDEXER_IMAGE \
  --platform managed \
  --region $REGION \
  --service-account $SERVICE_ACCOUNT \
  --set-env-vars "GCP_PROJECT_ID=$PROJECT_ID,DATA_STORE_ID=$DATA_STORE_ID,DATA_STORE_LOCATION=$DATA_STORE_LOCATION" \
  --no-allow-unauthenticated

# --- 2. AGENT SERVICE DEPLOYMENT ---
echo "--------------------------------------------"
echo "📦 Baue STUDY COMPANION AGENT (Dockerfile.app)..."
AGENT_IMAGE="gcr.io/$PROJECT_ID/study-companion-agent"

gcloud builds submit --tag $AGENT_IMAGE --dockerfile Dockerfile.app .

gcloud run deploy study-companion-agent \
  --image $AGENT_IMAGE \
  --platform managed \
  --region $REGION \
  --service-account $SERVICE_ACCOUNT \
  --set-env-vars "PROJECT_ID=$PROJECT_ID,GCS_BUCKET_NAME=$GCS_BUCKET_NAME,DATA_STORE_ID=$DATA_STORE_ID,DATA_STORE_LOCATION=$DATA_STORE_LOCATION" \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300

echo "--------------------------------------------"
echo "✅ Deployment abgeschlossen!"