#!/bin/bash
# AI Study Companion - MASTER Deployment Script (Simplified Structure)

echo "🚀 Starte Master Deployment..."

# --- Konfiguration ---
PROJECT_ID="ai-study-companion-480112"
REGION="europe-west1"
SERVICE_ACCOUNT="study-companion-sa@$PROJECT_ID.iam.gserviceaccount.com"
DATA_STORE_ID="asc-knowledge-base_1769181814756"
DATA_STORE_LOCATION="eu"
GCS_BUCKET_NAME="ai-study-companion"

# --- Vorbereitung: Authentifizierung sicherstellen ---
# Falls gcloud meckert, führe vorher manuell 'gcloud auth login' aus
gcloud config set project $PROJECT_ID

# --- 1. INDEXER SERVICE DEPLOYMENT ---
echo "--------------------------------------------"
echo "📦 Baue & Deploye INDEXER SERVICE..."
INDEXER_IMAGE="gcr.io/$PROJECT_ID/file-indexer-service"

# Wir bauen direkt aus dem Unterverzeichnis (nutzt dortiges Dockerfile)
gcloud builds submit indexer-service/ --tag $INDEXER_IMAGE

gcloud run deploy file-indexer-service-9404 \
  --image $INDEXER_IMAGE \
  --platform managed \
  --region $REGION \
  --service-account $SERVICE_ACCOUNT \
  --set-env-vars "GCP_PROJECT_ID=$PROJECT_ID,DATA_STORE_ID=$DATA_STORE_ID,DATA_STORE_LOCATION=$DATA_STORE_LOCATION" \
  --no-allow-unauthenticated

# --- 2. AGENT SERVICE DEPLOYMENT ---
echo "--------------------------------------------"
echo "📦 Baue & Deploye STUDY COMPANION AGENT..."
AGENT_IMAGE="gcr.io/$PROJECT_ID/study-companion-agent"

# Wir bauen aus dem Hauptverzeichnis (nutzt Dockerfile im Root)
gcloud builds submit . --tag $AGENT_IMAGE

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
echo "✅ Master Deployment erfolgreich abgeschlossen!"
URL=$(gcloud run services describe study-companion-agent --platform managed --region $REGION --format 'value(status.url)')
echo "🌐 Deine App ist jetzt live unter: $URL"