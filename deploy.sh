#!/bin/bash
# AI Study Companion - Cloud Run Deployment Script
# Führt Build und Deployment in einem Schritt aus

echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo ""
echo "🚀 Starting deployment process..."

# Konfiguration
PROJECT_ID="ai-study-companion-480112"
SERVICE_NAME="study-companion-agent"
REGION="europe-west1"
IMAGE_NAME="gcr.io/$PROJECT_ID/$SERVICE_NAME"
SERVICE_ACCOUNT="study-companion-sa@$PROJECT_ID.iam.gserviceaccount.com"
DATA_STORE_ID="ai-study-companion-data-store_1765190826355"
DATA_STORE_LOCATION="eu"

# Schritt 1: Image bauen
echo ""
echo "📦 Building Docker image..."
gcloud builds submit --tag $IMAGE_NAME

if [ $? -ne 0 ]; then
    echo "❌ Build failed!"
    exit 1
fi

echo "✅ Build successful!"

# Schritt 2: Zu Cloud Run deployen
echo ""
echo "🚀 Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
  --image $IMAGE_NAME \
  --platform managed \
  --region $REGION \
  --service-account $SERVICE_ACCOUNT \
  --set-env-vars "PROJECT_ID=$PROJECT_ID,GCS_BUCKET_NAME=ai-study-companion-bucket,DATA_STORE_ID=$DATA_STORE_ID,DATA_STORE_LOCATION=$DATA_STORE_LOCATION" \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300

if [ $? -ne 0 ]; then
    echo "❌ Deployment failed!"
    exit 1
fi

echo "✅ Deployment successful!"

# Schritt 3: URL abrufen
echo ""
echo "🌐 Service URL:"
URL=$(gcloud run services describe $SERVICE_NAME --platform managed --region $REGION --format 'value(status.url)')
echo $URL

echo ""
echo "✨ Deployment abgeschlossen!"
echo ""
echo "Öffne die URL im Browser: $URL"
