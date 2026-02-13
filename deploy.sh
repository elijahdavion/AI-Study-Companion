#!/bin/bash
# AI Study Companion - Cloud Run Deployment Script

echo "🚀 Starting deployment process..."

# --- Konfiguration (HIER KORRIGIERT) ---
PROJECT_ID="ai-study-companion-480112"
SERVICE_NAME="study-companion-agent"
REGION="europe-west1"
IMAGE_NAME="gcr.io/$PROJECT_ID/$SERVICE_NAME"
SERVICE_ACCOUNT="study-companion-sa@$PROJECT_ID.iam.gserviceaccount.com"

# KORREKTUR: Neue ID aus deinem Screenshot
DATA_STORE_ID="asc-knowledge-base_1769181814756" 
DATA_STORE_LOCATION="eu"
# KORREKTUR: Bucket-Name (Bitte prüfen, ob 'ai-study-companion' oder 'ai-study-companion-bucket' korrekt ist)
GCS_BUCKET_NAME="ai-study-companion"

# Schritt 1: Image bauen
echo "📦 Building Docker image..."
gcloud builds submit --tag $IMAGE_NAME

if [ $? -ne 0 ]; then
    echo "❌ Build failed!"
    exit 1
fi

# Schritt 2: Zu Cloud Run deployen
echo "🚀 Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
  --image $IMAGE_NAME \
  --platform managed \
  --region $REGION \
  --service-account $SERVICE_ACCOUNT \
  --set-env-vars "PROJECT_ID=$PROJECT_ID,GCS_BUCKET_NAME=$GCS_BUCKET_NAME,DATA_STORE_ID=$DATA_STORE_ID,DATA_STORE_LOCATION=$DATA_STORE_LOCATION" \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300

if [ $? -ne 0 ]; then
    echo "❌ Deployment failed!"
    exit 1
fi

echo "✅ Deployment successful!"

# URL abrufen
URL=$(gcloud run services describe $SERVICE_NAME --platform managed --region $REGION --format 'value(status.url)')
echo "🌐 Service URL: $URL"