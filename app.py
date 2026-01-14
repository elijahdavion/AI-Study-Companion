# app.py

import os
from datetime import datetime

from flask import Flask, request, jsonify, render_template

import vertexai
from vertexai.generative_models import GenerativeModel, Tool, grounding

from google.cloud import storage

from datetime import datetime
import re


# ---------------------------
# Configuration
# ---------------------------

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
VERTEX_REGION = os.getenv("VERTEX_REGION") or os.getenv("REGION") or "europe-west1"

DATA_STORE_ID = os.getenv("DATA_STORE_ID")  # should be datastore ID (recommended)
DATA_STORE_LOCATION = os.getenv("DATA_STORE_LOCATION")  # e.g. "eu" or "global" or "us"

GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME") or "ai-study-companion-bucket"
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


def discoveryengine_api_endpoint(location: str) -> str:
    """
    Discovery Engine endpoints:
      - global -> discoveryengine.googleapis.com
      - eu/us/etc -> eu-discoveryengine.googleapis.com, us-discoveryengine.googleapis.com, ...
    """
    if not location:
        raise ValueError("Missing DATA_STORE_LOCATION (expected e.g. 'eu' or 'global').")
    if location == "global":
        return "discoveryengine.googleapis.com"
    return f"{location}-discoveryengine.googleapis.com"


def datastore_resource_name(project_id: str, location: str, datastore_id_or_path: str) -> str:
    """
    Gemini grounding.VertexAISearch expects a datastore resource name.
    If the env already contains a full resource path, keep it.
    Otherwise build:
    projects/{project}/locations/{location}/collections/default_collection/dataStores/{id}
    """
    if not datastore_id_or_path:
        raise ValueError("Missing DATA_STORE_ID.")
    if datastore_id_or_path.startswith("projects/"):
        return datastore_id_or_path

    return (
        f"projects/{project_id}/locations/{location}/collections/default_collection/"
        f"dataStores/{datastore_id_or_path}"
    )


def datastore_branch_parent(project_id: str, location: str, datastore_id_or_path: str) -> str:
    """
    Document operations need the branches/default_branch scope.
    """
    ds = datastore_resource_name(project_id, location, datastore_id_or_path)
    return f"{ds}/branches/default_branch"


# Initialize Vertex AI (for Gemini model calls)
if PROJECT_ID:
    vertexai.init(project=PROJECT_ID, location=VERTEX_REGION)

app = Flask(__name__)


# ---------------------------
# System Prompt
# ---------------------------
SYSTEM_PROMPT = """
Sie sind ein hochspezialisierter KI-Studienbegleiter.

PRIMÄRE AUFGABE: Analysiere das angeforderte Dokument und erstelle eine strukturierte Markdown-Antwort.

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


# ---------------------------
# Routes
# ---------------------------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy"}), 200


@app.route("/list-files", methods=["GET"])
def list_files():
    try:
        if not PROJECT_ID:
            return jsonify({"error": "Server misconfiguration: Missing PROJECT_ID"}), 500

        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)

        files = []
        for blob in bucket.list_blobs():
            if blob.name.lower().endswith(".pdf"):
                files.append(
                    {
                        "name": blob.name,
                        "gs_path": f"gs://{GCS_BUCKET_NAME}/{blob.name}",
                        "size": blob.size,
                        "created": blob.time_created.isoformat() if blob.time_created else None,
                    }
                )

        files.sort(key=lambda x: x["created"] or "", reverse=True)
        return jsonify({"files": files}), 200

    except Exception as e:
        app.logger.error(f"Fehler beim Auflisten der Dateien: {e}")
        return jsonify({"error": "Fehler beim Auflisten der Dateien", "details": str(e)}), 500


@app.route("/upload", methods=["POST"])
def upload_pdf():
    try:
        if not PROJECT_ID or not DATA_STORE_ID or not DATA_STORE_LOCATION:
            return jsonify(
                {"error": "Server misconfiguration: Missing PROJECT_ID, DATA_STORE_ID, or DATA_STORE_LOCATION"}
            ), 500

        if "file" not in request.files:
            return jsonify({"error": "Keine Datei in der Anfrage gefunden."}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "Keine Datei ausgewählt."}), 400

        if not file.filename.lower().endswith(".pdf"):
            return jsonify({"error": "Nur PDF-Dateien sind erlaubt."}), 400

        if file.content_type not in ["application/pdf", "application/octet-stream"]:
            return jsonify({"error": "Ungültiger Dateityp. Bitte laden Sie eine PDF-Datei hoch."}), 400

        file_content = file.read()
        if len(file_content) > MAX_FILE_SIZE:
            return jsonify({"error": "Datei zu groß. Maximum: 100 MB"}), 400

        timestamp = datetime.now().strftime("%Y-%m-%d")
        original_name = file.filename.rsplit(".", 1)[0]
        unique_filename = generate_unique_filename(original_name, timestamp)

        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(unique_filename)
        blob.upload_from_string(file_content, content_type="application/pdf")

        gcs_uri = f"gs://{GCS_BUCKET_NAME}/{unique_filename}"

        # Die Datei wird automatisch vom Data Store verarbeitet.
        message = "Datei erfolgreich hochgeladen."

        return jsonify(
            {
                "success": True,
                "filename": unique_filename,
                "gcs_path": gcs_uri,
                "message": message,
            }
        ), 200

    except Exception as e:
        app.logger.error(f"Fehler beim Datei-Upload: {e}")
        return jsonify({"error": "Interner Serverfehler beim Upload", "details": str(e)}), 500


def generate_unique_filename(original_name: str, timestamp: str) -> str:
    """
    DATE_original.pdf  (or DATE_original(1).pdf ...)
    """
    try:
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)

        base_filename = f"{timestamp}_{original_name}.pdf"
        if not bucket.blob(base_filename).exists():
            return base_filename

        counter = 1
        while True:
            new_filename = f"{timestamp}_{original_name}({counter}).pdf"
            if not bucket.blob(new_filename).exists():
                return new_filename
            counter += 1

    except Exception as e:
        app.logger.error(f"Fehler beim Generieren des Dateinamens: {e}")
        return f"{timestamp}_{original_name}_{int(datetime.now().timestamp())}.pdf"



@app.route("/analyze", methods=["POST"])
def analyze_script():
    try:
        if not PROJECT_ID or not DATA_STORE_ID or not DATA_STORE_LOCATION:
            return jsonify({"error": "Server misconfiguration: Missing PROJECT_ID / DATA_STORE_ID / DATA_STORE_LOCATION"}), 500

        data = request.get_json(silent=True) or {}
        file_name = data.get("file_name")

        if not file_name:
            return jsonify({"error": "Fehlendes 'file_name' im JSON-Body."}), 400

        # Create a dynamic tool with a filter for the specific file
        tools = []
        try:
            ds_resource = datastore_resource_name(PROJECT_ID, DATA_STORE_LOCATION, DATA_STORE_ID)
            datastore_tool = Tool.from_retrieval(
                retrieval=grounding.Retrieval(
                    source=grounding.VertexAISearch(datastore=ds_resource)
                )
            )
            tools = [datastore_tool]
            app.logger.info(f"Tool created for file: {file_name}")
        except Exception as e:
            app.logger.error(f"Fehler beim Erstellen des dynamischen Tools: {e}")
            return jsonify({"error": "Fehler beim Vorbereiten der Analyse-Tools.", "details": str(e)}), 500

        display_name = file_name.split("/")[-1]
        if display_name.lower().endswith(".pdf"):
            display_name = display_name[:-4]

        topic_parts = display_name.split("_")
        main_topic = ""
        for i, part in enumerate(topic_parts):
            if part.startswith("Kapitel") and i + 1 < len(topic_parts):
                main_topic = topic_parts[i + 1]
                break
        if not main_topic:
            main_topic = display_name

        user_prompt = f"""Du analysierst AUSSCHLIESSLICH die Datei: {display_name}

Die Datei behandelt das Thema: "{main_topic}"

Die Analyse muss sich AUSSCHLIESSLICH auf das Thema "{main_topic}" beziehen.
"""

        model = GenerativeModel(
            model_name="gemini-1.0-pro",
            system_instruction=SYSTEM_PROMPT,
            tools=tools,
        )

        response = model.generate_content(user_prompt)

        full_text = ""
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if getattr(part, "text", None):
                    full_text += part.text

        used_sources = []
        if response.candidates:
            md = getattr(response.candidates[0], "grounding_metadata", None)
            if md and getattr(md, "grounding_chunks", None):
                for chunk in md.grounding_chunks:
                    if hasattr(chunk, "retrieved_context") and chunk.retrieved_context:
                        used_sources.append(chunk.retrieved_context.uri)
                    elif hasattr(chunk, "web") and chunk.web:
                        used_sources.append(chunk.web.uri)

        return jsonify({"analysis_result": full_text, "used_sources": used_sources}), 200

    except Exception as e:
        app.logger.error(f"Fehler bei der Analyse: {e}")
        return jsonify({"error": "Interner Serverfehler", "details": str(e)}), 500


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)
    app.logger.info("Starting AI Study Companion")
    app.logger.info(f"PROJECT_ID: {PROJECT_ID}")
    app.logger.info(f"VERTEX_REGION: {VERTEX_REGION}")
    app.logger.info(f"DATA_STORE_ID configured: {bool(DATA_STORE_ID)}")
    app.logger.info(f"DATA_STORE_LOCATION: {DATA_STORE_LOCATION}")

    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
