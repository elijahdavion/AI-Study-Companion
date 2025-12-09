# AI Study Companion - Vollständige Deployment-Anleitung

Diese Anleitung führt Sie Schritt für Schritt durch das Deployment Ihres AI Study Companion auf Google Cloud Run.

## Voraussetzungen

- Google Cloud SDK (`gcloud`) installiert und konfiguriert
- Docker installiert (für lokale Tests optional)
- Zugriff auf das Projekt: `ai-study-companion-480112`

---

## Schritt 1: Initiale Konfiguration

### 1.1 Projekt-ID setzen

```bash
gcloud config set project ai-study-companion-480112
```

### 1.2 Notwendige APIs aktivieren

```bash
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable aiplatform.googleapis.com
gcloud services enable storage.googleapis.com
gcloud services enable discoveryengine.googleapis.com
```

---

## Schritt 2: Service Account & IAM-Rollen konfigurieren

### 2.1 Service Account erstellen (falls noch nicht vorhanden)

```bash
gcloud iam service-accounts create study-companion-sa --display-name="AI Study Companion Service Account" --project=ai-study-companion-480112
```

### 2.2 IAM-Rollen zuweisen

```bash
# Vertex AI User - für Gemini API Zugriff
gcloud projects add-iam-policy-binding ai-study-companion-480112 --member="serviceAccount:study-companion-sa@ai-study-companion-480112.iam.gserviceaccount.com" --role="roles/aiplatform.user"

# Storage Object Viewer - für GCS Bucket Zugriff
gcloud projects add-iam-policy-binding ai-study-companion-480112 --member="serviceAccount:study-companion-sa@ai-study-companion-480112.iam.gserviceaccount.com" --role="roles/storage.objectViewer"

# Discovery Engine Viewer - für Vertex AI Search Data Store Zugriff
gcloud projects add-iam-policy-binding ai-study-companion-480112 --member="serviceAccount:study-companion-sa@ai-study-companion-480112.iam.gserviceaccount.com" --role="roles/discoveryengine.viewer"

# Logs Writer - für Cloud Logging
gcloud projects add-iam-policy-binding ai-study-companion-480112 --member="serviceAccount:study-companion-sa@ai-study-companion-480112.iam.gserviceaccount.com" --role="roles/logging.logWriter"
```

---

## Schritt 3: Projektdateien vorbereiten

### 3.1 Dockerfile erstellen

Erstellen Sie eine Datei namens `Dockerfile` im Projektverzeichnis:

```dockerfile
FROM python:3.11-slim

# Setze Arbeitsverzeichnis
WORKDIR /app

# Kopiere requirements und installiere Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiere Anwendungscode
COPY app.py .

# Exponiere Port (Cloud Run nutzt PORT env var)
EXPOSE 8080

# Starte Anwendung mit Gunicorn
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
```

### 3.2 .dockerignore erstellen

Erstellen Sie eine Datei namens `.dockerignore`:

```
__pycache__
*.pyc
*.pyo
*.pyd
.Python
env/
venv/
.git
.gitignore
README.md
.dockerignore
Dockerfile
*.log
.DS_Store
```

---

## Schritt 4: Container Image bauen und deployen

### 4.1 Mit Cloud Build bauen

> [!IMPORTANT]
> Dieser Befehl baut das Container Image direkt in der Cloud (kein lokales Docker erforderlich).

```bash
gcloud builds submit --tag gcr.io/ai-study-companion-480112/study-companion-agent
```

### 4.2 Auf Cloud Run deployen

> [!IMPORTANT]
> Die `DATA_STORE_ID` muss als **vollständiger Resource Name** übergeben werden!

```bash
gcloud run deploy study-companion-agent \
  --image gcr.io/ai-study-companion-480112/study-companion-agent \
  --platform managed \
  --region europe-west1 \
  --service-account study-companion-sa@ai-study-companion-480112.iam.gserviceaccount.com \
  --set-env-vars GCP_PROJECT_ID=ai-study-companion-480112,DATA_STORE_ID=projects/ai-study-companion-480112/locations/eu/collections/default_collection/dataStores/ai-study-companion-data-store_1765190826355 \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300
```

**Parameter-Erklärung:**
- `--image`: Das gebaute Container Image
- `--platform managed`: Vollständig verwaltete Cloud Run Plattform
- `--region`: Deployment-Region (europe-west1)
- `--service-account`: Der Service Account mit den IAM-Berechtigungen
- `--set-env-vars`: Umgebungsvariablen (DATA_STORE_ID als vollständiger Resource Name!)
- `--allow-unauthenticated`: Erlaubt öffentliche HTTP-Anfragen (für Tests)
- `--memory`: 1GB RAM Zuweisung
- `--cpu`: 1 vCPU
- `--timeout`: 300 Sekunden maximale Request-Dauer

---

## Schritt 5: Service-URL abrufen

```bash
gcloud run services describe study-companion-agent --platform managed --region europe-west1 --format 'value(status.url)'
```

Die Ausgabe ist Ihre Service-URL, z.B.: `https://study-companion-agent-xxxxx-ew.a.run.app`

---

## Schritt 6: Service testen

### 6.1 Mit curl testen

```bash
# Speichern Sie die URL in einer Variable
SERVICE_URL=$(gcloud run services describe study-companion-agent --platform managed --region europe-west1 --format 'value(status.url)')

# URL anzeigen
echo "Service URL: $SERVICE_URL"

# Testen
curl -X POST $SERVICE_URL/analyze \
  -H "Content-Type: application/json" \
  -d '{"file_name": "gs://ai-study-companion-bucket/BWS.pdf"}'
```

> [!TIP]
> Der curl-Befehl sollte eine strukturierte Markdown-Antwort mit Zusammenfassung, Themen und Lernzielen zurückgeben.

### 6.2 Logs anzeigen

```bash
gcloud run services logs read study-companion-agent --platform managed --region europe-west1 --limit 50
```

---

## Troubleshooting

### Deployment schlägt fehl

```bash
# Zeige Build-Logs an
gcloud builds list --limit 5
gcloud builds log BUILD_ID
```

### Service startet nicht

```bash
# Prüfe Service-Status
gcloud run services describe study-companion-agent --platform managed --region europe-west1

# Zeige detaillierte Logs
gcloud run services logs read study-companion-agent --platform managed --region europe-west1 --limit 100
```

### IAM-Berechtigungen prüfen

```bash
gcloud projects get-iam-policy ai-study-companion-480112 --flatten="bindings[].members" --filter="bindings.members:study-companion-sa@ai-study-companion-480112.iam.gserviceaccount.com"
```

---

## Befehle zum Aktualisieren

### Code-Änderungen deployen

```bash
# Image neu bauen
gcloud builds submit --tag gcr.io/ai-study-companion-480112/study-companion-agent

# Neues Image deployen (Cloud Run erkennt automatisch neue Version)
gcloud run deploy study-companion-agent --image gcr.io/ai-study-companion-480112/study-companion-agent --platform managed --region europe-west1
```

### Umgebungsvariablen aktualisieren

```bash
gcloud run services update study-companion-agent --platform managed --region europe-west1 --set-env-vars GCP_PROJECT_ID=ai-study-companion-480112,DATA_STORE_ID=NEW_DATA_STORE_ID
```

### Service löschen

```bash
gcloud run services delete study-companion-agent --platform managed --region europe-west1
```

---

## Wichtige Hinweise

> [!WARNING]
> Der Parameter `--allow-unauthenticated` macht den Service öffentlich zugänglich. Für Produktionsumgebungen sollten Sie Authentifizierung aktivieren.

### Für authentifizierten Zugriff:

```bash
# Service mit Authentifizierung deployen
gcloud run deploy study-companion-agent --image gcr.io/ai-study-companion-480112/study-companion-agent --platform managed --region europe-west1 --service-account study-companion-sa@ai-study-companion-480112.iam.gserviceaccount.com --set-env-vars GCP_PROJECT_ID=ai-study-companion-480112,DATA_STORE_ID=ai-study-companion-data-store_1765190826355 --no-allow-unauthenticated --memory 1Gi --cpu 1 --timeout 300

# Mit Authentifizierung testen
curl -X POST https://YOUR-SERVICE-URL/analyze -H "Authorization: Bearer $(gcloud auth print-identity-token)" -H "Content-Type: application/json" -d "{\"file_name\": \"gs://ai-study-companion-bucket/BWS.pdf\"}"
```

---

## Schnell-Referenz: Alle Befehle in Reihenfolge

### Für Cloud Shell (empfohlen)

```bash
# 1. In den Projektordner wechseln
cd ~/ai-study-companion

# 2. Projekt setzen (wenn noch nicht gesetzt)
gcloud config set project ai-study-companion-480112

# 3. Image bauen
gcloud builds submit --tag gcr.io/ai-study-companion-480112/study-companion-agent

# 4. Deployen
gcloud run deploy study-companion-agent \
  --image gcr.io/ai-study-companion-480112/study-companion-agent \
  --platform managed \
  --region europe-west1 \
  --service-account study-companion-sa@ai-study-companion-480112.iam.gserviceaccount.com \
  --set-env-vars GCP_PROJECT_ID=ai-study-companion-480112,DATA_STORE_ID=projects/ai-study-companion-480112/locations/eu/collections/default_collection/dataStores/ai-study-companion-data-store_1765190826355 \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300

# 5. Testen
SERVICE_URL=$(gcloud run services describe study-companion-agent --platform managed --region europe-west1 --format 'value(status.url)') && \
echo "Service URL: $SERVICE_URL" && \
curl -X POST $SERVICE_URL/analyze \
  -H "Content-Type: application/json" \
  -d '{"file_name": "gs://ai-study-companion-bucket/BWS.pdf"}'
```

### Einmalige Setup-Befehle (falls noch nicht gemacht)

Führen Sie diese nur einmal beim ersten Setup aus:

```bash
# APIs aktivieren
gcloud services enable cloudbuild.googleapis.com run.googleapis.com aiplatform.googleapis.com storage.googleapis.com discoveryengine.googleapis.com

# Service Account erstellen (kann Fehler werfen wenn existiert - ignorieren)
gcloud iam service-accounts create study-companion-sa --display-name="AI Study Companion Service Account"

# IAM-Rollen zuweisen
gcloud projects add-iam-policy-binding ai-study-companion-480112 --member="serviceAccount:study-companion-sa@ai-study-companion-480112.iam.gserviceaccount.com" --role="roles/aiplatform.user"
gcloud projects add-iam-policy-binding ai-study-companion-480112 --member="serviceAccount:study-companion-sa@ai-study-companion-480112.iam.gserviceaccount.com" --role="roles/storage.objectViewer"
gcloud projects add-iam-policy-binding ai-study-companion-480112 --member="serviceAccount:study-companion-sa@ai-study-companion-480112.iam.gserviceaccount.com" --role="roles/discoveryengine.viewer"
gcloud projects add-iam-policy-binding ai-study-companion-480112 --member="serviceAccount:study-companion-sa@ai-study-companion-480112.iam.gserviceaccount.com" --role="roles/logging.logWriter"
```
