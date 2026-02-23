import os
import logging
from flask import Flask, request, jsonify, render_template
from google.cloud import storage
import vertexai
from vertexai.generative_models import GenerativeModel, Tool, grounding
from google.cloud import discoveryengine_v1 as discoveryengine
from google.api_core import exceptions

# --- Configuration ---
# Load configuration from environment variables.
# Note: These names must match the names set in deploy.sh
PROJECT_ID = os.getenv("PROJECT_ID")
GCP_REGION = os.getenv("GCP_REGION", "europe-west1") # Used for Vertex AI client
DATA_STORE_LOCATION = os.getenv("DATA_STORE_LOCATION") # Used for Data Store path
DATA_STORE_ID = os.getenv("DATA_STORE_ID")
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")

# --- App Initialization ---
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# --- Vertex AI Initialization ---
try:
    if PROJECT_ID:
        vertexai.init(project=PROJECT_ID, location=GCP_REGION)
        app.logger.info("Vertex AI initialized successfully.")
    else:
        app.logger.warning("PROJECT_ID not found. Vertex AI not initialized.")
except Exception as e:
    app.logger.error(f"Error initializing Vertex AI: {e}")

# --- Tool Definition for Data Store ---
tools = []
if PROJECT_ID and DATA_STORE_ID and DATA_STORE_LOCATION:
    # Check if the user provided the full path or just the ID
    if DATA_STORE_ID.startswith("projects/"):
        datastore_resource_name = DATA_STORE_ID
        app.logger.info("Using full resource name for Data Store.")
    else:
        # Construct the full datastore resource name from the short ID
        datastore_resource_name = (
            f"projects/{PROJECT_ID}/locations/{DATA_STORE_LOCATION}/collections/default_collection/"
            f"dataStores/{DATA_STORE_ID}"
        )
        app.logger.info("Constructing full resource name for Data Store from ID.")

    datastore_tool = Tool.from_retrieval(
        retrieval=grounding.Retrieval(
            source=grounding.VertexAISearch(datastore=datastore_resource_name)
        )
    )
    tools = [datastore_tool]
    app.logger.info("Vertex AI Search tool configured successfully.")
else:
    app.logger.warning("PROJECT_ID, DATA_STORE_ID, or DATA_STORE_LOCATION not found. Vertex AI Search tool not configured.")

# --- System Prompt ---
SYSTEM_PROMPT = """
Sie sind ein hochspezialisierter KI-Studienbegleiter. Ihre Aufgabe ist es, die vom Data Store Tool bereitgestellten Informationen zu analysieren und eine strukturierte Markdown-Antwort zu generieren.

**ABSOLUTE FORMATIERUNGSREGELN:**

1.  **STRUKTUR:** Die Antwort MUSS exakt die folgenden DREI Abschnitte in dieser Reihenfolge enthalten: `## Zusammenfassung`, `## Thematische Übersicht`, `## Lernziele`.
2.  **ÜBERSCHRIFTEN:** JEDER der drei Abschnitte MUSS mit einer Markdown-Überschrift der Ebene 2 (`##`) beginnen. Es darf keine andere Art von Überschrift (z.B. `###` oder `**fett**`) verwendet werden.
3.  **ABSTÄNDE:** Zwischen einer Überschrift und dem darauffolgenden Text MUSS genau eine Leerzeile sein.
4.  **EINLEITUNGSSÄTZE:** Beginnen Sie die Antwort IMMER direkt mit `## Zusammenfassung`. Schreiben Sie KEINE einleitenden Sätze oder Vorworte wie "Gerne...".
5.  **LISTEN:**
    *   Verwenden Sie für ALLE Listen, insbesondere bei der "Thematische Übersicht" und den "Lernzielen", AUSSCHLIESSLICH Aufzählungszeichen mit einem Bindestrich (`-`).
    *   Verwenden Sie KEINE nummerierten Listen (z.B. `1.`, `2.`).
    *   Unterpunkte werden mit zwei Leerzeichen eingerückt (`  -`).

**BEISPIEL FÜR KORREKTE FORMATIERUNG:**

## Zusammenfassung
Dies ist eine prägnante Zusammenfassung des Inhalts.

## Thematische Übersicht
- Hauptpunkt eins
  - Unterpunkt 1.1
  - Unterpunkt 1.2
- Hauptpunkt zwei

## Lernziele
- Der Studierende kann Konzept A definieren.
- Der Studierende kann Prozess B analysieren.
"""

# --- Routes ---
@app.route("/")
def home():
    """Serves the main HTML page."""
    return render_template("index.html")

@app.route("/files", methods=["GET"])
def list_files():
    """Lists only PDF files that are already indexed in the data store."""
    if not all([GCS_BUCKET_NAME, PROJECT_ID, DATA_STORE_ID, DATA_STORE_LOCATION]):
        return jsonify({"error": "Server misconfiguration"}), 500
    
    try:
        # 1. Holen der Liste aller bereits indizierten URIs aus Vertex AI Search
        indexed_uris = []
        final_datastore_id = DATA_STORE_ID.split('/')[-1]
        parent = f"projects/{PROJECT_ID}/locations/{DATA_STORE_LOCATION}/collections/default_collection/dataStores/{final_datastore_id}/branches/0"
        
        endpoint = "eu-discoveryengine.googleapis.com" if DATA_STORE_LOCATION.lower() in ["eu", "europe-west1"] else f"{DATA_STORE_LOCATION}-discoveryengine.googleapis.com"
        client = discoveryengine.DocumentServiceClient(client_options={"api_endpoint": endpoint})
        
        page_result = client.list_documents(parent=parent)
        for doc in page_result:
            uri = getattr(doc, 'content', {}).uri or getattr(doc, 'uri', "")
            if uri: indexed_uris.append(uri)

        # 2. Abgleich mit dem GCS Bucket
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blobs = bucket.list_blobs()
        
        files = [
            {"name": blob.name, "path": f"gs://{GCS_BUCKET_NAME}/{blob.name}"}
            for blob in blobs
            if blob.name.lower().endswith(".pdf") and f"gs://{GCS_BUCKET_NAME}/{blob.name}" in indexed_uris
        ]
        
        files.sort(key=lambda x: x['name'])
        return jsonify({"files": files}), 200
    except Exception as e:
        app.logger.error(f"Error listing indexed files: {e}")
        return jsonify({"error": "Could not list files.", "details": str(e)}), 500

@app.route("/upload", methods=["POST"])
def upload_file():
    """Handles PDF file uploads and saves them to GCS."""
    if not GCS_BUCKET_NAME:
        return jsonify({"error": "Server misconfiguration: GCS_BUCKET_NAME not set"}), 500

    if "file" not in request.files:
        return jsonify({"error": "No file part in the request."}), 400
    
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected for uploading."}), 400
        
    if file and file.filename.lower().endswith(".pdf"):
        try:
            storage_client = storage.Client(project=PROJECT_ID)
            bucket = storage_client.bucket(GCS_BUCKET_NAME)
            blob = bucket.blob(file.filename)
            
            blob.upload_from_file(file)
            
            return jsonify({
                "success": True, 
                "message": f"File '{file.filename}' uploaded successfully.",
                "path": f"gs://{GCS_BUCKET_NAME}/{file.filename}",
                "filename": file.filename
            }), 201
        except Exception as e:
            app.logger.error(f"Error uploading file: {e}")
            return jsonify({"error": "File upload failed.", "details": str(e)}), 500
    else:
        return jsonify({"error": "Invalid file type. Only PDFs are allowed."}), 400

@app.route("/analyze", methods=["POST"])
def analyze_script():
    """Analyzes a specific document from the data store."""
    if not tools:
        return jsonify({"error": "Server misconfiguration: Analysis tool not available."}), 500

    data = request.get_json()
    file_path = data.get("file_path")
    
    if not file_path:
        return jsonify({"error": "Missing 'file_path' in the request body."}), 400

    app.logger.info(f"Received analysis request for: {file_path}")
    file_name = file_path.split("/")[-1]

    try:
        # KORREKTUR: Aggressiverer Prompt, um Tool-Nutzung zu erzwingen
        user_prompt = (
            f"NUTZE DAS DATA_STORE_TOOL! Suche in deinem Index nach der Datei '{file_name}'. "
            f"Extrahiere alle Informationen aus '{file_name}' und erstelle daraus: "
            f"1. Zusammenfassung, 2. Thematische Übersicht, 3. Lernziele. "
            f"Antworte NUR mit den Fakten aus dieser Datei."
        )
        
        # 1. Modell definieren (Kurzname für europe-west1)
        model = GenerativeModel(
            model_name='gemini-2.5-flash',
            system_instruction=SYSTEM_PROMPT,
            tools=tools
        )

        # 2. TOOL_CONFIG hinzufügen: Zwingt die KI, das Such-Tool tatsächlich zu verwenden
        from vertexai.generative_models import ToolConfig
        
        tool_config = ToolConfig(
            forced_function_calling_config=ToolConfig.ForcedFunctionCallingConfig(
                mode=ToolConfig.ForcedFunctionCallingConfig.Mode.ANY,
                allowed_function_names=[] # Leer lassen für Retrieval-Tools
            )
        )

        # 3. Content generieren (mit der tool_config!)
        response = model.generate_content(
            user_prompt,
            tool_config=tool_config
        )
        # NEU: Sicherheitscheck (verhindert Abstürze bei leeren Antworten)
        if not response.candidates or not response.candidates[0].content.parts:
            return jsonify({
                "error": "Keine Inhalte generiert.",
                "details": "Das Modell konnte keine Informationen extrahieren. Eventuell ist die Indizierung noch nicht fertig."
            }), 404
        # Prüfung auf "Finish Reason" (oft SAFETY oder OTHER, wenn Grounding fehlschlägt)
        if response.candidates[0].finish_reason != 1: # 1 = STOP (Erfolg)
            app.logger.warning(f"Modell beendete mit Grund: {response.candidates[0].finish_reason}")

        full_text = ""
        if response and response.candidates:
            full_text = "".join(part.text for part in response.candidates[0].content.parts if hasattr(part, "text"))

        # Falls Text leer ist, ist das Dokument meist noch nicht im Vektor-Index verfügbar
        if not full_text.strip():
            return jsonify({
                "error": "Inhalt noch nicht verfügbar.",
                "details": "Die Datei wurde gefunden, aber die KI kann die Inhalte noch nicht lesen. Bitte warte ca. 2-3 Minuten, bis die automatische Indizierung abgeschlossen ist."
            }), 404

        # Safely extract grounding metadata
        used_sources = []
        if response.candidates and hasattr(response.candidates[0], 'grounding_metadata') and response.candidates[0].grounding_metadata:
            for chunk in response.candidates[0].grounding_metadata.grounding_chunks:
                if hasattr(chunk, "retrieved_context") and chunk.retrieved_context:
                    used_sources.append(chunk.retrieved_context.uri)

        app.logger.info(f"Successfully analyzed '{file_name}'.")
        return jsonify({
            "analysis_result": full_text,
            "used_sources": list(set(used_sources))
        }), 200

    except Exception as e:
        app.logger.error(f"Error during analysis for {file_name}: {e}")
        return jsonify({"error": "Internal server error during analysis.", "details": str(e)}), 500


@app.route("/check_file_status", methods=['POST'])
def check_file_status():
    """Checks if a document has been indexed in the data store."""
    data = request.get_json()
    gcs_uri = data.get("gcs_uri")

    if not all([gcs_uri, PROJECT_ID, DATA_STORE_LOCATION, DATA_STORE_ID]):
        return jsonify({"error": "Server misconfiguration, missing environment variables."}), 500

    try:
        # Get the actual data store ID if the full resource name was provided
        final_datastore_id = DATA_STORE_ID.split('/')[-1]
        
        # Construct the parent branch resource name
        parent = (
            f"projects/{PROJECT_ID}/locations/{DATA_STORE_LOCATION}/collections/default_collection/"
            f"dataStores/{final_datastore_id}/branches/0"
        )

        # KORREKTUR: Expliziter EU-Endpoint für Discovery Engine
        client_options = None
        if DATA_STORE_LOCATION:
            # Für Europa ist 'eu' der korrekte Präfix für die Discovery Engine API
            endpoint = "eu-discoveryengine.googleapis.com" if DATA_STORE_LOCATION.lower() in ["eu", "europe-west1"] else f"{DATA_STORE_LOCATION}-discoveryengine.googleapis.com"
            client_options = {"api_endpoint": endpoint}

        client = discoveryengine.DocumentServiceClient(client_options=client_options)
        
        # FIX: Wir laden die Liste und prüfen die URI manuell, um den 'filter' Fehler zu umgehen
        request_list = discoveryengine.ListDocumentsRequest(parent=parent)
        page_result = client.list_documents(request=request_list)
        
        for doc in page_result:
            if (hasattr(doc, 'content') and doc.content.uri == gcs_uri) or (hasattr(doc, 'uri') and doc.uri == gcs_uri):
                app.logger.info(f"Document '{gcs_uri}' found in index.")
                return jsonify({"status": "INDEXED"}), 200
        
        return jsonify({"status": "PROCESSING"}), 202

    except Exception as e:
        app.logger.error(f"Error checking document status for '{gcs_uri}': {e}")
        return jsonify({"status": "FAILED", "details": str(e)}), 500


if __name__ == "__main__":
    app.logger.info("Starting AI Study Companion...")
    app.logger.info(f"Project ID: {PROJECT_ID}")
    app.logger.info(f"Region: {GCP_REGION}")
    app.logger.info(f"Data Store ID configured: {bool(DATA_STORE_ID)}")
    app.logger.info(f"GCS Bucket Name configured: {bool(GCS_BUCKET_NAME)}")
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
