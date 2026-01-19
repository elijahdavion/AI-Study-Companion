import os
import logging
from flask import Flask, request, jsonify, render_template
from google.cloud import storage
import vertexai
from vertexai.generative_models import GenerativeModel, Tool, grounding

# --- Configuration ---
# Load configuration from environment variables
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
GCP_REGION = os.getenv("GCP_REGION", "europe-west1")
DATA_STORE_ID = os.getenv("DATA_STORE_ID")
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")

# --- App Initialization ---
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# --- Vertex AI Initialization ---
try:
    if GCP_PROJECT_ID:
        vertexai.init(project=GCP_PROJECT_ID, location=GCP_REGION)
        app.logger.info("Vertex AI initialized successfully.")
    else:
        app.logger.warning("GCP_PROJECT_ID not found. Vertex AI not initialized.")
except Exception as e:
    app.logger.error(f"Error initializing Vertex AI: {e}")

# --- Tool Definition for Data Store ---
tools = []
if GCP_PROJECT_ID and DATA_STORE_ID:
    # Construct the full datastore resource name
    datastore_resource_name = (
        f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/collections/default_collection/"
        f"dataStores/{DATA_STORE_ID}"
    )
    datastore_tool = Tool.from_retrieval(
        retrieval=grounding.Retrieval(
            source=grounding.VertexAISearch(datastore=datastore_resource_name)
        )
    )
    tools = [datastore_tool]
    app.logger.info("Vertex AI Search tool configured.")
else:
    app.logger.warning("GCP_PROJECT_ID or DATA_STORE_ID not found. Vertex AI Search tool not configured.")

# --- System Prompt ---
SYSTEM_PROMPT = """
Sie sind ein hochspezialisierter KI-Studienbegleiter. Ihre Aufgabe ist es, die **vom Data Store Tool bereitgestellten Informationen** zu analysieren und eine strukturierte Markdown-Antwort zu generieren.

**WICHTIG:** Das Data Store Tool hat die notwendigen Dokument-Auszüge bereits abgerufen. Sie müssen sich **NICHT** für einen fehlenden Zugriff auf GCS oder lokale Dateien entschuldigen, sondern müssen die abgerufenen Inhalte direkt für die Generierung nutzen.

Die Antwort muss exakt DREI spezifische Abschnitte enthalten:

1.  **Zusammenfassung:** Eine prägnante, aber vollständige Zusammenfassung der wichtigsten Konzepte und Argumente.
2.  **Thematische Übersicht:** Eine hierarchische (nummerierte oder verschachtelte) Gliederung der im Skript behandelten Hauptthemen und Unterpunkte.
3.  **Lernziele:** Eine Liste von mindestens fünf spezifischen, messbaren Lernzielen (SMART-Prinzip) in Form von Aktionsverben ("Der Studierende kann...", "Definieren Sie...", "Analysieren Sie...").

Die Antwort MUSS ausschließlich im Markdown-Format erfolgen.

Bitte lasse Voworte raus wie: Gerne fasse ich die wichtigsten Inhalte des vorliegenden Skripts zusammen. Die bereitgestellten Informationen behandeln grundlegende Aspekte der Bildaufnahme und Videosignalübertragung.

Gebe nur oben genannten 3 Punkte an.
"""

# --- Routes ---
@app.route("/")
def home():
    """Serves the main HTML page."""
    return render_template("index.html")

@app.route("/files", methods=["GET"])
def list_files():
    """Lists all PDF files in the GCS bucket."""
    if not GCS_BUCKET_NAME:
        return jsonify({"error": "Server misconfiguration: GCS_BUCKET_NAME not set"}), 500
    
    try:
        storage_client = storage.Client(project=GCP_PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        
        blobs = bucket.list_blobs()
        files = [
            {"name": blob.name, "path": f"gs://{GCS_BUCKET_NAME}/{blob.name}"}
            for blob in blobs
            if blob.name.lower().endswith(".pdf")
        ]
        
        files.sort(key=lambda x: x['name'])
        
        return jsonify({"files": files}), 200
    except Exception as e:
        app.logger.error(f"Error listing files: {e}")
        return jsonify({"error": "Could not list files from bucket.", "details": str(e)}), 500

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
            storage_client = storage.Client(project=GCP_PROJECT_ID)
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

    file_name = file_path.split("/")[-1]

    try:
        user_prompt = f"Analysiere den Inhalt der Datei '{file_name}'. Nutze dafür das Data Store Tool. Erstelle die drei geforderten Abschnitte (Zusammenfassung, Thematische Übersicht, Lernziele) basierend auf den abgerufenen Fakten."

        model = GenerativeModel(
            model_name='gemini-2.5-flash',
            system_instruction=SYSTEM_PROMPT,
            tools=tools
        )

        response = model.generate_content(user_prompt)

        full_text = "".join(part.text for part in response.candidates[0].content.parts if part.text)

        used_sources = []
        if response.candidates and hasattr(response.candidates[0], 'grounding_metadata') and response.candidates[0].grounding_metadata:
            for chunk in response.candidates[0].grounding_metadata.grounding_chunks:
                if hasattr(chunk, "retrieved_context") and chunk.retrieved_context:
                    used_sources.append(chunk.retrieved_context.uri)

        return jsonify({
            "analysis_result": full_text,
            "used_sources": list(set(used_sources))
        }), 200

    except Exception as e:
        app.logger.error(f"Error during analysis: {e}")
        return jsonify({"error": "Internal server error during analysis.", "details": str(e)}), 500

if __name__ == "__main__":
    app.logger.info("Starting AI Study Companion...")
    app.logger.info(f"Project ID: {GCP_PROJECT_ID}")
    app.logger.info(f"Region: {GCP_REGION}")
    app.logger.info(f"Data Store ID configured: {bool(DATA_STORE_ID)}")
    app.logger.info(f"GCS Bucket Name configured: {bool(GCS_BUCKET_NAME)}")
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
