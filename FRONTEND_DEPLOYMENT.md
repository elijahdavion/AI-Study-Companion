# Frontend Deployment - Schnellanleitung

## Dateien für Cloud Shell

Sie müssen 3 Dateien in Cloud Shell aktualisieren/erstellen:

### 1. app.py aktualisieren

Die neue `app.py` hat eine Home-Route hinzugefügt. Sie können die Datei von Ihrem lokalen Rechner kopieren oder in Cloud Shell bearbeiten.

### 2. Dockerfile aktualisieren

Neue Zeile hinzugefügt: `COPY templates/ ./templates/`

### 3. templates/index.html erstellen

Neues Web-Interface im `templates/` Ordner.

---

## Option A: Dateien manuell in Cloud Shell erstellen

```bash
cd ~/ai-study-companion

# Templates-Ordner erstellen
mkdir -p templates

# Dateien bearbeiten mit nano oder vim
nano app.py          # Neue Version einfügen
nano Dockerfile      # COPY templates/ Zeile hinzufügen
nano templates/index.html  # HTML-Code einfügen
```

---

## Option B: Cloud Shell Editor nutzen

1. Öffnen Sie Cloud Shell: https://shell.cloud.google.com
2. Klicken Sie auf "Editor öffnen" (Stift-Symbol)
3. Navigieren Sie zu `~/ai-study-companion`
4. Erstellen Sie die Dateien dort
5. Kopieren Sie den Inhalt aus den lokalen Dateien

---

## Deployment (nach dem Aktualisieren der Dateien)

```bash
cd ~/ai-study-companion

# Image neu bauen
gcloud builds submit --tag gcr.io/ai-study-companion-480112/study-companion-agent

# Deployen
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

# URL abrufen und im Browser öffnen
gcloud run services describe study-companion-agent --platform managed --region europe-west1 --format 'value(status.url)'
```

---

## Was Sie sehen werden

Nach dem Deployment:
- Öffnen Sie die Service-URL im Browser
- Eine moderne Weboberfläche mit Gradient-Design
- Eingabefeld für PDF-Dateinamen (vorausgefüllt mit Ihrem BWS.pdf)
- "Skript analysieren" Button
- Ergebnisse werden schön formatiert angezeigt

---

## Lokale Dateien

Alle Dateien sind lokal gespeichert unter:
```
C:\Users\Elijah\.gemini\antigravity\AI Study Companion\
├── app.py (NEU - mit Home-Route)
├── Dockerfile (NEU - mit templates/)
├── templates/
│   └── index.html (NEU)
├── requirements.txt
├── .dockerignore
└── deployment_guide.md
```
