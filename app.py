import os
from flask import Flask, request, jsonify, render_template
import vertexai
from vertexai.generative_models import GenerativeModel, Tool, grounding
from google.cloud import storage

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
if PROJECT_ID and DATA_STORE_ID:
    try:
        # Definiere das Retrieval-Tool für Vertex AI Search
        datastore_tool = Tool.from_retrieval(
            retrieval=grounding.Retrieval(
                source=grounding.VertexAISearch(
                    datastore=DATA_STORE_ID
                )
            )
        )
        tools = [datastore_tool]
    except Exception as e:
        import logging
        logging.error(f"Fehler beim Initialisieren des Data Store Tools: {e}")
        tools = []

# --- Spezifischer Prompt (System Instruction) ---
SYSTEM_PROMPT = """
Sie sind ein hochspezialisierter KI-Studienbegleiter mit STRIKTER Qualitätskontrolle. 

PRIMÄRE AUFGABE: Analysiere nur das angeforderte Dokument und erstelle eine strukturierte Markdown-Antwort.

KRITISCHE REGEL - VALIDIERUNG DER QUELLE:
- BEVOR Sie mit der Analyse beginnen, überprüfen Sie IMMER die Themenrelevanz
- Wenn die vom Retrieval-Tool bereitgestellten Inhalte NICHT zum angeforderten Thema passen (z.B. falsche Kapitel, falsches Dokument):
  → Brechen Sie sofort ab mit: "Die Datei konnte nicht analysiert werden, da das Retrieval-System die falschen Inhalte liefert."
- Fahren Sie NUR fort, wenn die Inhalte themenbezogen korrekt sind

FORMATIERUNG DER ANTWORT:
Die Antwort muss exakt DREI spezifische Abschnitte enthalten:

1.  **Zusammenfassung:** Eine prägnante, aber vollständige Zusammenfassung der wichtigsten Konzepte.
    - Verwenden Sie EINFACHE, verständliche Sprache
    - Schreiben Sie in kurzen, klaren Sätzen
    - Beginnen Sie mit der Überschrift "## Zusammenfassung"
    
2.  **Thematische Übersicht:** Eine hierarchische Gliederung der im Skript behandelten Hauptthemen.
    - Beginnen Sie mit der Überschrift "## Thematische Übersicht"
    - Verwenden Sie NUR Aufzählungszeichen (Bullet Points), KEINE Nummerierungen
    - Jeder Hauptpunkt beginnt mit einem Bindestrich (-)
    - Jeder Unterpunkt beginnt mit zwei Leerzeichen, dann Bindestrich (  -)
    - JEDER Punkt MUSS auf einer NEUEN ZEILE stehen
    
3.  **Lernziele:** Eine Liste von mindestens fünf spezifischen, messbaren Lernzielen (SMART-Prinzip).
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

@app.route("/health", methods=["GET"])
def health_check():
    """
    Health check endpoint für Cloud Run.
    """
    return jsonify({"status": "healthy"}), 200

@app.route("/list-files", methods=["GET"])
def list_files():
    """
    Listet alle PDF-Dateien im GCS Bucket auf.
    """
    try:
        if not PROJECT_ID:
            return jsonify({"error": "Server misconfiguration: Missing GCP_PROJECT_ID"}), 500

        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        
        blobs = bucket.list_blobs()
        files = []
        
        for blob in blobs:
            if blob.name.lower().endswith('.pdf'):
                files.append({
                    "name": blob.name,
                    "gs_path": f"gs://{GCS_BUCKET_NAME}/{blob.name}",
                    "size": blob.size,
                    "created": blob.time_created.isoformat() if blob.time_created else None
                })
        
        # Sortiere nach neuesten zuerst
        files.sort(key=lambda x: x['created'], reverse=True)
        
        return jsonify({"files": files}), 200

    except Exception as e:
        app.logger.error(f"Fehler beim Auflisten der Dateien: {e}")
        return jsonify({"error": "Fehler beim Auflisten der Dateien", "details": str(e)}), 500

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

        # Die Indexierung erfolgt automatisch durch den Data Store.
        indexing_message = "Datei erfolgreich hochgeladen und wird automatisch indiziert. Die Analyse ist in wenigen Minuten verfügbar."

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
        
        # Extrahiere den Dateinamen aus dem GCS-Pfad
        # Z.B. "gs://bucket/2026-01-05_Kapitel3_SpeicherungundÜbertragung_202411_v2.7.pdf" 
        # -> "Kapitel3_SpeicherungundÜbertragung"
        display_name = file_name.split('/')[-1]  # Get filename from path
        if display_name.endswith('.pdf'):
            display_name = display_name[:-4]  # Remove .pdf extension
        
        # Extract the main topic from the filename
        # E.g., "2026-01-05_Kapitel3_SpeicherungundÜbertragung_202411_v2.7.pdf"
        # -> "Speicherung und Übertragung" or "Kapitel3"
        topic_parts = display_name.split('_')
        # Usually format is: DATE_CHAPTER_TOPIC_DATE_VERSION
        main_topic = ""
        for i, part in enumerate(topic_parts):
            if part.startswith('Kapitel'):
                if i + 1 < len(topic_parts):
                    main_topic = topic_parts[i + 1]
                break
        
        if not main_topic:
            main_topic = display_name
        
        # Der Prompt muss das Modell anweisen, das Tool zu benutzen
        user_prompt = f"""Du analysierst AUSSCHLIESSLICH die Datei: {display_name}

Die Datei behandelt das Thema: "{main_topic}"

WICHTIG - QUALITÄTSKONTROLLE:
1. Nutze das Retrieval-Tool um Inhalte abzurufen
2. ÜBERPRÜFE SOFORT: Behandeln die abgerufenen Inhalte das Thema "{main_topic}"?
3. WENN NICHT (z.B. wenn sie von "Bildaufnahme", "Kamerasensoren" oder anderen Themen sprechen):
   → Lehne sofort ab und antworte: "Die Datei '{display_name}' konnte nicht analysiert werden, da das Retrieval-System die falschen Inhalte liefert."
4. WENN JA, fahre fort mit der vollständigen Analyse

Deine Aufgabe ist die UMFASSENDE Analyse:
1. Identifiziere ALLE Kapitel und Hauptthemen
2. Gehe systematisch JEDES Kapitel durch
3. Extrahiere die Kerninhalte
4. Erstelle Zusammenfassung, Themenübersicht und Lernziele

Die Analyse muss sich AUSSCHLIESSLICH auf das Thema "{main_topic}" beziehen."""

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
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    logger.info(f"Starting AI Study Companion")
    logger.info(f"GCP_PROJECT_ID: {PROJECT_ID}")
    logger.info(f"DATA_STORE_ID configured: {bool(DATA_STORE_ID)}")
    logger.info(f"Tools initialized: {len(tools) > 0}")
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
