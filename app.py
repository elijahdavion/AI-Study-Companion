import os
from flask import Flask, request, jsonify, render_template
import vertexai
from vertexai.generative_models import GenerativeModel, Tool, grounding
from google.cloud import storage
from google.cloud import discoveryengine_v1
from datetime import datetime
import re

# --- Konfiguration ---
PROJECT_ID = os.getenv("GCP_PROJECT_ID") 
REGION = "europe-west1" 
DATA_STORE_ID = os.getenv("DATA_STORE_ID")
GCS_BUCKET_NAME = "ai-study-companion-bucket"
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

# Initialisiere Vertex AI
if PROJECT_ID:
    vertexai.init(project=PROJECT_ID, location=REGION)

# --- Tool Definition für Data Store ---
tools = []
if PROJECT_ID:
    # Definiere das Retrieval-Tool für Vertex AI Search
    datastore_tool = Tool.from_retrieval(
        retrieval=grounding.Retrieval(
            source=grounding.VertexAISearch(
                datastore=DATA_STORE_ID
            )
        )
    )
    tools = [datastore_tool]

# --- Spezifischer Prompt (System Instruction) ---
SYSTEM_PROMPT = """
Sie sind ein hochspezialisierter KI-Studienbegleiter. Ihre Aufgabe ist es, die **vom Data Store Tool bereitgestellten Informationen** zu analysieren und eine strukturierte Markdown-Antwort zu generieren.

**WICHTIG:** Das Data Store Tool hat die notwendigen Dokument-Auszüge bereits abgerufen. Sie müssen sich **NICHT** für einen fehlenden Zugriff auf GCS oder lokale Dateien entschuldigen, sondern müssen die abgerufenen Inhalte direkt für die Generierung nutzen.

Die Antwort muss exakt DREI spezifische Abschnitte enthalten:

1.  **Zusammenfassung:** Eine prägnante, aber vollständige Zusammenfassung der wichtigsten Konzepte und Argumente.
    - Verwenden Sie EINFACHE, verständliche Sprache
    - Schreiben Sie in kurzen, klaren Sätzen
    - Beginnen Sie mit der Überschrift "## Zusammenfassung"
    
2.  **Thematische Übersicht:** Eine hierarchische Gliederung der im Skript behandelten Hauptthemen und Unterpunkte.
    - Beginnen Sie mit der Überschrift "## Thematische Übersicht"
    - Verwenden Sie NUR Aufzählungszeichen (Bullet Points), KEINE Nummerierungen
    - Jeder Hauptpunkt beginnt mit einem Bindestrich (-)
    - Jeder Unterpunkt beginnt mit zwei Leerzeichen, dann Bindestrich (  -)
    - JEDER Punkt MUSS auf einer NEUEN ZEILE stehen
    
3.  **Lernziele:** Eine Liste von mindestens fünf spezifischen, messbaren Lernzielen (SMART-Prinzip) in Form von Aktionsverben.
    - Beginnen Sie mit der Überschrift "## Lernziele"
    - Sprechen Sie den Leser mit "Du" an
    - Format: "Du kannst..." oder "Du verstehst..."

**FORMATIERUNGSREGELN:**
- Geben Sie den Text DIREKT als Markdown aus
- Verwenden Sie KEINE Markdown Code-Blöcke (```markdown```)
- Verwenden Sie KEINE einleitenden Sätze oder Vorworte
- Beginnen Sie direkt mit "## Zusammenfassung"
- Achten Sie auf korrekte Zeilenumbrüche zwischen allen Listenpunkten
"""

app = Flask(__name__)

@app.route("/")
def home():
    """
    Startseite mit Web-Interface.
    """
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload_pdf():
    """
    HTTP-Endpunkt zum Hochladen von PDF-Dateien zu GCS mit automatischer Indexierung.
    """
    try:
        if not PROJECT_ID or not DATA_STORE_ID:
            return jsonify({"error": "Server misconfiguration: Missing GCP_PROJECT_ID or DATA_STORE_ID"}), 500

        # Überprüfe, ob eine Datei im Request vorhanden ist
        if "file" not in request.files:
            return jsonify({"error": "Keine Datei in der Anfrage gefunden."}), 400

        file = request.files["file"]

        if file.filename == "":
            return jsonify({"error": "Keine Datei ausgewählt."}), 400

        # Validiere Dateityp
        if not file.filename.lower().endswith(".pdf"):
            return jsonify({"error": "Nur PDF-Dateien sind erlaubt."}), 400

        if file.content_type not in ["application/pdf", "application/octet-stream"]:
            return jsonify({"error": "Ungültiger Dateityp. Bitte laden Sie eine PDF-Datei hoch."}), 400

        # Lese Datei in den Speicher
        file_content = file.read()

        # Überprüfe Dateigröße
        if len(file_content) > MAX_FILE_SIZE:
            return jsonify({"error": f"Datei zu groß. Maximum: 100 MB"}), 400

        # Generiere eindeutigen Dateinamen mit Timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d")
        original_name = file.filename.rsplit(".", 1)[0]  # Entferne .pdf
        unique_filename = generate_unique_filename(original_name, timestamp)

        # Lade Datei zu GCS hoch
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(unique_filename)
        blob.upload_from_string(file_content, content_type="application/pdf")

        # Triggere automatische Indexierung
        try:
            trigger_indexing(unique_filename)
            indexing_message = "Datei erfolgreich hochgeladen. Indexierung läuft... (5-30 Minuten)"
        except Exception as e:
            app.logger.warning(f"Indexierung konnte nicht triggert werden: {e}")
            indexing_message = "Datei hochgeladen, aber Indexierung konnte nicht gestartet werden."

        return jsonify({
            "success": True,
            "filename": unique_filename,
            "gcs_path": f"gs://{GCS_BUCKET_NAME}/{unique_filename}",
            "message": indexing_message
        }), 200

    except Exception as e:
        app.logger.error(f"Fehler beim Datei-Upload: {e}")
        return jsonify({"error": "Interner Serverfehler beim Upload", "details": str(e)}), 500

def generate_unique_filename(original_name, timestamp):
    """
    Generiert einen eindeutigen Dateinamen mit Timestamp-Präfix.
    Falls die Datei bereits existiert, wird ein Suffix hinzugefügt: (1), (2), etc.
    """
    try:
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        
        base_filename = f"{timestamp}_{original_name}.pdf"
        
        # Überprüfe, ob Datei bereits existiert
        if not bucket.blob(base_filename).exists():
            return base_filename
        
        # Falls existiert, füge Nummern hinzu
        counter = 1
        while True:
            new_filename = f"{timestamp}_{original_name}({counter}).pdf"
            if not bucket.blob(new_filename).exists():
                return new_filename
            counter += 1
            
    except Exception as e:
        app.logger.error(f"Fehler beim Generieren des Dateinamens: {e}")
        # Fallback auf Timestamp allein
        return f"{timestamp}_{original_name}_{int(datetime.now().timestamp())}.pdf"

def trigger_indexing(gcs_path):
    """
    Triggert die automatische Indexierung für die hochgeladene Datei.
    """
    try:
        client = discoveryengine_v1.DocumentServiceClient()
        
        # Extrahiere Data Store ID Komponenten
        # Format: projects/{project}/locations/{location}/collections/default_collection/dataStores/{datastore_id}
        parent = f"projects/{PROJECT_ID}/locations/{REGION}/collections/default_collection/dataStores/{DATA_STORE_ID.split('/')[-1]}"
        
        # Erstelle ein Dokument-Objekt
        from google.cloud.discoveryengine_v1.types import Document
        
        document = Document(
            id=gcs_path.replace("/", "-").replace(".", "-"),
            structured_data={"title": gcs_path},
            raw_document=discoveryengine_v1.RawDocument(
                file_type_=discoveryengine_v1.RawDocument.FileType.PDF,
                file_data=discoveryengine_v1.FileData(
                    mime_type="application/pdf",
                    gcs_uri=f"gs://{GCS_BUCKET_NAME}/{gcs_path}"
                )
            )
        )
        
        # Erstelle oder update das Dokument im Data Store
        operation = client.create_document(request={"parent": parent, "document": document})
        app.logger.info(f"Indexierung gestartet für {gcs_path}: Operation {operation.name}")
        
    except Exception as e:
        app.logger.error(f"Fehler beim Triggern der Indexierung: {e}")
        raise

@app.route("/analyze", methods=["POST"])
def analyze_script():
    """
    HTTP-Endpunkt, der eine Datei analysiert und strukturierte Ergebnisse liefert.
    """
    try:
        if not PROJECT_ID or not DATA_STORE_ID:
             return jsonify({"error": "Server misconfiguration: Missing GCP_PROJECT_ID or DATA_STORE_ID"}), 500

        data = request.get_json()
        file_name = data.get("file_name")

        if not file_name:
            return jsonify({"error": "Fehlendes 'file_name' im JSON-Body."}), 400
        
        
        # Der Prompt muss das Modell anweisen, das Tool zu benutzen
        user_prompt = f"""Analysiere die Datei '{file_name}' vollständig.
        
Deine Aufgabe ist eine UMFASSENDE Analyse des GESAMTEN Dokuments.
1. Identifiziere ZUERST alle Kapitel und Hauptthemen aus dem Inhaltsverzeichnis oder der Struktur.
2. Gehe JEDES Kapitel durch und extrahiere die Kerninhalte.
3. Erstelle daraus die drei geforderten Abschnitte (Zusammenfassung, Themenübersicht, Lernziele).

WICHTIG: Die "Thematische Übersicht" muss ALLE Hauptkapitel des Skripts abdecken, nicht nur den Anfang oder Ausschnitte! Nutze das Data Store Tool mehrfach oder umfassend, um alles zu finden."""

        # Initialisiere das Modell mit den Tools
        model = GenerativeModel(
            model_name='gemini-2.5-pro',
            system_instruction=SYSTEM_PROMPT,
            tools=tools
        )

        # Generiere den Inhalt
        response = model.generate_content(user_prompt)

        # Extrahiere den Text sicher (auch bei mehreren Parts)
        full_text = ""
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if part.text:
                    full_text += part.text

        # Extrahiere Quellen (falls vorhanden)
        used_sources = []
        if response.candidates and response.candidates[0].grounding_metadata.grounding_chunks:
            for chunk in response.candidates[0].grounding_metadata.grounding_chunks:
                # Vertex AI Search liefert 'retrieved_context'
                if hasattr(chunk, "retrieved_context") and chunk.retrieved_context:
                    used_sources.append(chunk.retrieved_context.uri)
                # Fallback für Web Search (falls jemals genutzt)
                elif hasattr(chunk, "web") and chunk.web:
                    used_sources.append(chunk.web.uri)

        return jsonify({
            "analysis_result": full_text,
            "used_sources": used_sources
        }), 200

    except Exception as e:
        app.logger.error(f"Fehler bei der Analyse: {e}")
        return jsonify({"error": "Interner Serverfehler", "details": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
