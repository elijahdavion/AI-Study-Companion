#!/bin/bash
# AI Study Companion - MASTER Deployment Script (Agent & Indexer)

echo "🚀 Starting Master Deployment..."

# --- Gemeinsame Konfiguration ---
PROJECT_ID="ai-study-companion-480112"
REGION="europe-west1"
SERVICE_ACCOUNT="study-companion-sa@$PROJECT_ID.iam.gserviceaccount.com"

# Korrekte IDs aus deinem System
DATA_STORE_ID="asc-knowledge-base_1769181814756"
DATA_STORE_LOCATION="eu"
GCS_BUCKET_NAME="ai-study-companion"

# --- 1. INDEXER SERVICE DEPLOYMENT ---
echo "--------------------------------------------"
echo "📦 Building & Deploying INDEXER SERVICE..."
echo "--------------------------------------------"
INDEXER_IMAGE="gcr.io/$PROJECT_ID/file-indexer-service"

# Build & Push für den Indexer (aus dem Unterordner)
gcloud builds submit indexer-service --tag $INDEXER_IMAGE

# Deployment des Indexers
gcloud run deploy file-indexer-service-9404 \
  --image $INDEXER_IMAGE \
  --platform managed \
  --region $REGION \
  --service-account $SERVICE_ACCOUNT \
  --set-env-vars "GCP_PROJECT_ID=$PROJECT_ID,DATA_STORE_ID=$DATA_STORE_ID,DATA_STORE_LOCATION=$DATA_STORE_LOCATION" \
  --no-allow-unauthenticated

# --- 2. AGENT SERVICE DEPLOYMENT ---
echo "--------------------------------------------"
echo "📦 Building & Deploying STUDY COMPANION AGENT..."
echo "--------------------------------------------"
AGENT_IMAGE="gcr.io/$PROJECT_ID/study-companion-agent"

# Build & Push für den Agent (aus dem Hauptverzeichnis)
gcloud builds submit --tag $AGENT_IMAGE

# Deployment des Agents
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
echo "🌐 Deine App ist erreichbar unter: $URL"
echo "--------------------------------------------"