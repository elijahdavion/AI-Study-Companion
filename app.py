import os
import logging
from flask import Flask, request, jsonify, render_template
from google.cloud import storage
import vertexai
from vertexai.generative_models import GenerativeModel, Tool, grounding
from google.cloud import discoveryengine_v1 as discoveryengine
from google.api_core import exceptions

# --- Configuration ---
PROJECT_ID = os.getenv("PROJECT_ID")
GCP_REGION = os.getenv("GCP_REGION", "europe-west1") 
DATA_STORE_LOCATION = os.getenv("DATA_STORE_LOCATION") 
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
    if DATA_STORE_ID.startswith("projects/"):
        datastore_resource_name = DATA_STORE_ID
    else:
        datastore_resource_name = (
            f"projects/{PROJECT_ID}/locations/{DATA_STORE_LOCATION}/collections/default_collection/"
            f"dataStores/{DATA_STORE_ID}"
        )

    datastore_tool = Tool.from_retrieval(
        retrieval=grounding.Retrieval(
            source=grounding.VertexAISearch(datastore=datastore_resource_name)
        )
    )
    tools = [datastore_tool]
    app.logger.info(f"Vertex AI Search tool configured: {datastore_resource_name}")
else:
    app.logger.warning("Missing config for Vertex AI Search tool.")

# --- System Prompt ---
SYSTEM_PROMPT = """
Sie sind ein hochspezialisierter KI-Studienbegleiter. Ihre Aufgabe ist es, die vom Data Store Tool bereitgestellten Informationen zu analysieren und eine strukturierte Markdown-Antwort zu generieren.

**ABSOLUTE FORMATIERUNGSREGELN:**
1. **STRUKTUR:** Abschnitte `## Zusammenfassung`, `## Thematische Übersicht`, `## Lernziele` (exakt diese Reihenfolge).
2. **ÜBERSCHRIFTEN:** Nur Ebene 2 (##). Eine Leerzeile nach jeder Überschrift.
3. **EINLEITUNG:** Keine Vorworte. Direkt mit `## Zusammenfassung` beginnen.
4. **LISTEN:** Nur Bindestriche (-). Keine Nummern. Unterpunkte mit zwei Leerzeichen eingerückt.
"""

# --- Routes ---
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/files", methods=["GET"])
def list_files():
    if not GCS_BUCKET_NAME:
        return jsonify({"error": "GCS_BUCKET_NAME not set"}), 500
    try:
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blobs = bucket.list_blobs()
        
        # Nur indizierte Dateien anzeigen (Abgleich mit Discovery Engine)
        final_datastore_id = DATA_STORE_ID.split('/')[-1]
        parent = f"projects/{PROJECT_ID}/locations/{DATA_STORE_LOCATION}/collections/default_collection/dataStores/{final_datastore_id}/branches/0"
        endpoint = "eu-discoveryengine.googleapis.com" if DATA_STORE_LOCATION.lower() in ["eu", "europe-west1"] else f"{DATA_STORE_LOCATION}-discoveryengine.googleapis.com"
        client = discoveryengine.DocumentServiceClient(client_options={"api_endpoint": endpoint})
        
        indexed_uris = [getattr(doc, 'content', {}).uri or getattr(doc, 'uri', "") for doc in client.list_documents(parent=parent)]

        files = [
            {"name": blob.name, "path": f"gs://{GCS_BUCKET_NAME}/{blob.name}"}
            for blob in blobs
            if blob.name.lower().endswith(".pdf") and f"gs://{GCS_BUCKET_NAME}/{blob.name}" in indexed_uris
        ]
        files.sort(key=lambda x: x['name'])
        return jsonify({"files": files}), 200
    except Exception as e:
        app.logger.error(f"Error listing files: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    try:
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(file.filename)
        blob.upload_from_file(file)
        return jsonify({"success": True, "filename": file.filename, "path": f"gs://{GCS_BUCKET_NAME}/{file.filename}"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/analyze", methods=["POST"])
def analyze_script():
    if not tools:
        return jsonify({"error": "Tool not available"}), 500
    data = request.get_json()
    file_path = data.get("file_path")
    file_name = file_path.split("/")[-1]

    try:
        user_prompt = f"Analysiere den Inhalt der Datei '{file_name}'. Nutze dafür das Data Store Tool. Erstelle Zusammenfassung, Übersicht und Lernziele."
        
        model = GenerativeModel(
            model_name='gemini-2.5-flash',
            system_instruction=SYSTEM_PROMPT,
            tools=tools
        )

        response = model.generate_content(user_prompt)

        # Prüfung auf Antwort-Inhalt (Regression Fix)
        if not response.candidates or not response.candidates[0].content.parts:
            return jsonify({"error": "Inhalt noch nicht verfügbar.", "details": "Warte bitte 60 Sekunden auf die Vektorisierung."}), 404

        full_text = "".join(part.text for part in response.candidates[0].content.parts if hasattr(part, "text"))

        if not full_text.strip():
            return jsonify({"error": "Leere Antwort.", "details": "Die KI konnte keine Fakten extrahieren."}), 404

        # Grounding Metadata Extraktion
        used_sources = []
        try:
            if hasattr(response.candidates[0], 'grounding_metadata') and response.candidates[0].grounding_metadata:
                for chunk in response.candidates[0].grounding_metadata.grounding_chunks:
                    if hasattr(chunk, "retrieved_context"):
                        used_sources.append(chunk.retrieved_context.uri)
        except:
            used_sources = [file_path] # Fallback

        return jsonify({
            "analysis_result": full_text,
            "used_sources": list(set(used_sources)) if used_sources else [file_path]
        }), 200

    except Exception as e:
        app.logger.error(f"Error during analysis: {e}")
        return jsonify({"error": "Analyse-Fehler", "details": str(e)}), 500

@app.route("/check_file_status", methods=['POST'])
def check_file_status():
    data = request.get_json()
    gcs_uri = data.get("gcs_uri")
    try:
        final_datastore_id = DATA_STORE_ID.split('/')[-1]
        parent = f"projects/{PROJECT_ID}/locations/{DATA_STORE_LOCATION}/collections/default_collection/dataStores/{final_datastore_id}/branches/0"
        endpoint = "eu-discoveryengine.googleapis.com" if DATA_STORE_LOCATION.lower() in ["eu", "europe-west1"] else f"{DATA_STORE_LOCATION}-discoveryengine.googleapis.com"
        client = discoveryengine.DocumentServiceClient(client_options={"api_endpoint": endpoint})
        
        for doc in client.list_documents(parent=parent):
            if (hasattr(doc, 'content') and doc.content.uri == gcs_uri) or (hasattr(doc, 'uri') and doc.uri == gcs_uri):
                return jsonify({"status": "INDEXED"}), 200
        return jsonify({"status": "PROCESSING"}), 202
    except Exception as e:
        return jsonify({"status": "FAILED", "details": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)