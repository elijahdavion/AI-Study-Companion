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

# --- Removed generic retrieval tool - using direct search API instead ---
# The Tool.from_retrieval() approach searches ALL documents without filtering.
# We'll use Discovery Engine Search API directly with filter expressions.

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

@app.route("/check-indexing/<path:filename>", methods=["GET"])
def check_indexing_status(filename):
    """
    Prüft, ob ein Dokument in Discovery Engine indexiert wurde.
    """
    try:
        from google.cloud.discoveryengine_v1 import DocumentServiceClient
        
        client = DocumentServiceClient()
        datastore_id = DATA_STORE_ID.split('/')[-1] if '/' in DATA_STORE_ID else DATA_STORE_ID
        parent = f"projects/{PROJECT_ID}/locations/{REGION}/collections/default_collection/dataStores/{datastore_id}"
        
        # Erstelle Dokument-ID basierend auf Dateinamen
        doc_id = filename.replace("/", "-").replace(".", "-")
        doc_name = f"{parent}/documents/{doc_id}"
        
        try:
            document = client.get_document(name=doc_name)
            return jsonify({
                "indexed": True,
                "document_id": doc_id,
                "filename": filename,
                "status": "Document is indexed"
            }), 200
        except Exception as e:
            return jsonify({
                "indexed": False,
                "document_id": doc_id,
                "filename": filename,
                "status": f"Document not found in index: {str(e)}"
            }), 404
            
    except Exception as e:
        app.logger.error(f"Fehler beim Prüfen des Indexierungsstatus: {e}")
        return jsonify({"error": str(e)}), 500

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

        # Triggere automatische Indexierung
        try:
            trigger_indexing(unique_filename)
            indexing_message = "Datei erfolgreich hochgeladen und Indexierung gestartet. Indexierung benötigt 5-30 Minuten."
            app.logger.info(f"✓ Upload und Indexierung erfolgreich für {unique_filename}")
        except Exception as e:
            app.logger.error(f"❌ Indexierung fehlgeschlagen für {unique_filename}: {e}")
            indexing_message = f"Datei hochgeladen, aber Indexierung fehlgeschlagen: {str(e)}. Bitte prüfen Sie die Cloud Run Logs."

        return jsonify({
            "success": True,
            "filename": unique_filename,
            "gcs_path": f"gs://{GCS_BUCKET_NAME}/{unique_filename}",
            "message": indexing_message
        }), 200

    except Exception as e:
        app.logger.error(f"Fehler beim Datei-Upload: {e}")
        return jsonify({"error": "Interner Serverfehler beim Upload", "details": str(e)}), 500

def extract_document_metadata(filename):
    """
    Extrahiert Metadaten aus dem Dateinamen.
    Format: YYYY-MM-DD_KapitelX_Topic_Date_Version.pdf
    Gibt ein Dict mit chapter, topic, date, version zurück.
    """
    metadata = {
        "filename": filename,
        "chapter": "",
        "topic": "",
        "date": "",
        "version": ""
    }
    
    try:
        # Entferne .pdf Extension
        name_without_ext = filename.replace('.pdf', '')
        parts = name_without_ext.split('_')
        
        # Extrahiere Datum (erstes Teil im Format YYYY-MM-DD)
        if len(parts) > 0 and re.match(r'\d{4}-\d{2}-\d{2}', parts[0]):
            metadata["date"] = parts[0]
        
        # Suche nach Kapitel und Topic
        for i, part in enumerate(parts):
            if part.startswith('Kapitel') or part.startswith('kapitel'):
                metadata["chapter"] = part
                # Nächster Teil ist oft das Topic
                if i + 1 < len(parts):
                    metadata["topic"] = parts[i + 1]
                break
        
        # Falls kein Kapitel gefunden, nutze zweiten Teil als Topic
        if not metadata["topic"] and len(parts) > 1:
            metadata["topic"] = parts[1]
        
        # Suche nach Version (vX.Y Format)
        for part in parts:
            if re.match(r'v\d+\.\d+', part, re.IGNORECASE):
                metadata["version"] = part
                break
                
    except Exception as e:
        app.logger.warning(f"Fehler beim Extrahieren von Metadaten aus {filename}: {e}")
    
    return metadata

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

def search_document_content(filename, query_text, max_results=10):
    """
    Sucht nach Inhalten in einem spezifischen Dokument mittels Discovery Engine Search API.
    Verwendet Filter-Expressions um nur das angegebene Dokument zu durchsuchen.
    """
    try:
        from google.cloud.discoveryengine_v1 import SearchServiceClient
        from google.cloud.discoveryengine_v1.types import SearchRequest
        
        client = SearchServiceClient()
        
        # Extrahiere Data Store ID
        datastore_id = DATA_STORE_ID.split('/')[-1] if '/' in DATA_STORE_ID else DATA_STORE_ID
        serving_config = f"projects/{PROJECT_ID}/locations/{REGION}/collections/default_collection/dataStores/{datastore_id}/servingConfigs/default_config"
        
        # Erstelle Filter-Expression für das spezifische Dokument
        # Format: filename: "exact_filename.pdf"
        filter_expr = f'filename: "{filename}"'
        
        app.logger.info(f"Searching with filter: {filter_expr}")
        app.logger.info(f"Query: {query_text}")
        app.logger.info(f"Serving config: {serving_config}")
        
        # Erstelle Search Request
        request = SearchRequest(
            serving_config=serving_config,
            query=query_text,
            filter=filter_expr,
            page_size=max_results,
        )
        
        # Führe Suche aus
        response = client.search(request=request)
        
        # Sammle Ergebnisse
        results = []
        result_count = 0
        
        for result in response.results:
            result_count += 1
            try:
                if hasattr(result, 'document'):
                    doc = result.document
                    
                    # Versuche verschiedene Quellen für Textinhalte
                    # 1. Extractive Answers (beste Qualität)
                    if hasattr(doc, 'derived_struct_data'):
                        derived_data = doc.derived_struct_data
                        
                        if 'extractive_answers' in derived_data:
                            for answer in derived_data['extractive_answers']:
                                if 'content' in answer:
                                    results.append(answer['content'])
                        
                        # 2. Snippets (gute Zusammenfassung)
                        if 'snippets' in derived_data:
                            for snippet in derived_data['snippets']:
                                if 'snippet' in snippet:
                                    results.append(snippet['snippet'])
                        
                        # 3. Link (Fallback für Struktur)
                        if 'link' in derived_data:
                            link_text = derived_data.get('link', '')
                            if link_text and link_text not in results:
                                results.append(link_text)
                    
                    # 4. Struct Data als Fallback
                    if hasattr(doc, 'struct_data') and len(results) == 0:
                        struct_data = doc.struct_data
                        if struct_data:
                            app.logger.info(f"Using struct_data: {struct_data}")
                            
            except Exception as e:
                app.logger.warning(f"Fehler beim Verarbeiten eines Suchergebnisses: {e}")
                continue
        
        app.logger.info(f"Processed {result_count} results, extracted {len(results)} text chunks for {filename}")
        
        # Wenn keine Ergebnisse mit Filter, versuche ohne Filter als Fallback
        if len(results) == 0:
            app.logger.warning(f"No results with filter, trying without filter...")
            try:
                request_no_filter = SearchRequest(
                    serving_config=serving_config,
                    query=f"{filename} {query_text}",
                    page_size=max_results,
                )
                response_no_filter = client.search(request=request_no_filter)
                
                result_count_no_filter = 0
                for result in response_no_filter.results:
                    result_count_no_filter += 1
                    if hasattr(result, 'document'):
                        doc = result.document
                        # Log welche Dokumente gefunden wurden
                        if hasattr(doc, 'struct_data'):
                            struct_data = dict(doc.struct_data)
                            app.logger.info(f"Found document: {struct_data.get('filename', 'unknown')}")
                        
                        if hasattr(doc, 'derived_struct_data'):
                            derived_data = doc.derived_struct_data
                            if 'snippets' in derived_data:
                                for snippet in derived_data['snippets']:
                                    if 'snippet' in snippet:
                                        results.append(snippet['snippet'])
                
                app.logger.info(f"Fallback search found {result_count_no_filter} documents, extracted {len(results)} text chunks")
                
                # Wenn immernoch keine Ergebnisse, zeige was verfügbar ist
                if len(results) == 0:
                    app.logger.error(f"NO DOCUMENTS FOUND AT ALL in datastore. The datastore might be empty or indexing hasn't started.")
                
            except Exception as e:
                app.logger.error(f"Fallback search also failed: {e}")
        
        return results
        
    except Exception as e:
        app.logger.error(f"Fehler bei der Dokumentensuche: {e}")
        app.logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        app.logger.error(f"Traceback: {traceback.format_exc()}")
        return []

def trigger_indexing(gcs_path):
    """
    Triggert die automatische Indexierung für die hochgeladene Datei mit Metadaten.
    Verwendet import_documents statt create_document für bessere Zuverlässigkeit.
    """
    try:
        app.logger.info(f"Starting indexing for: {gcs_path}")
        app.logger.info(f"PROJECT_ID: {PROJECT_ID}")
        app.logger.info(f"REGION: {REGION}")
        app.logger.info(f"DATA_STORE_ID: {DATA_STORE_ID}")
        
        # Extrahiere Metadaten aus dem Dateinamen
        metadata = extract_document_metadata(gcs_path)
        app.logger.info(f"Extracted metadata: {metadata}")
        
        # Versuche import_documents statt create_document
        # Dies ist zuverlässiger für GCS-basierte Datenquellen
        from google.cloud.discoveryengine_v1 import DocumentServiceClient
        from google.cloud.discoveryengine_v1.types import ImportDocumentsRequest, GcsSource
        
        client = DocumentServiceClient()
        
        # Extrahiere Data Store ID
        datastore_id = DATA_STORE_ID.split('/')[-1] if '/' in DATA_STORE_ID else DATA_STORE_ID
        parent = f"projects/{PROJECT_ID}/locations/{REGION}/collections/default_collection/dataStores/{datastore_id}/branches/default_branch"
        
        app.logger.info(f"Parent path: {parent}")
        app.logger.info(f"GCS URI: gs://{GCS_BUCKET_NAME}/{gcs_path}")
        
        # Erstelle GCS Source
        gcs_source = GcsSource(
            input_uris=[f"gs://{GCS_BUCKET_NAME}/{gcs_path}"],
            data_schema="custom"  # Verwende custom schema für Metadaten
        )
        
        # Erstelle Import Request
        request = ImportDocumentsRequest(
            parent=parent,
            gcs_source=gcs_source,
            reconciliation_mode=ImportDocumentsRequest.ReconciliationMode.INCREMENTAL,
        )
        
        # Starte Import Operation
        operation = client.import_documents(request=request)
        
        app.logger.info(f"✓ Indexierung erfolgreich gestartet für {gcs_path}")
        app.logger.info(f"Operation name: {operation.operation.name}")
        app.logger.info(f"Metadaten: filename={metadata['filename']}, topic={metadata['topic']}, chapter={metadata['chapter']}")
        
        return True
        
    except Exception as e:
        app.logger.error(f"❌ FEHLER beim Triggern der Indexierung für {gcs_path}")
        app.logger.error(f"Error type: {type(e).__name__}")
        app.logger.error(f"Error message: {str(e)}")
        import traceback
        app.logger.error(f"Full traceback:\n{traceback.format_exc()}")
        # Re-raise so upload endpoint can catch it
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
        
        # Extrahiere den tatsächlichen Dateinamen für bessere Filterung
        actual_filename = file_name.split('/')[-1] if '/' in file_name else file_name
        
        app.logger.info(f"Starting analysis for file: {actual_filename}")
        app.logger.info(f"Topic: {main_topic}")
        
        # Suche direkt nach Inhalten aus dem spezifischen Dokument
        search_query = f"Analysiere das Dokument über {main_topic}"
        document_chunks = search_document_content(actual_filename, search_query, max_results=20)
        
        if not document_chunks:
            error_msg = f"Keine Inhalte für Datei '{actual_filename}' gefunden. Das Dokument wurde vermutlich noch nicht indexiert. Bitte warten Sie 5-30 Minuten nach dem Upload und versuchen Sie es erneut."
            app.logger.error(error_msg)
            app.logger.error(f"Upload time vs. now: Check if document was uploaded recently")
            return jsonify({
                "error": error_msg,
                "indexing_info": "Discovery Engine benötigt 5-30 Minuten um hochgeladene Dokumente zu indexieren. Bitte warten Sie und versuchen Sie es später erneut."
            }), 404
        
        # Kombiniere die gefundenen Chunks zu einem Kontext
        document_context = "\n\n".join(document_chunks[:15])  # Limitiere auf erste 15 Chunks
        
        app.logger.info(f"Retrieved {len(document_chunks)} chunks, using first 15 for analysis")
        app.logger.info(f"Context length: {len(document_context)} characters")
        
        # Erstelle Prompt mit dem tatsächlichen Dokumentinhalt
        user_prompt = f"""Du analysierst die Datei: "{actual_filename}"

Erwartetes Thema: "{main_topic}"

Hier sind die relevanten Inhalte aus dem Dokument:

{document_context}

Deine Aufgabe ist die UMFASSENDE Analyse basierend auf diesen Inhalten:
1. Identifiziere ALLE Kapitel und Hauptthemen
2. Gehe systematisch JEDES Kapitel durch
3. Extrahiere die Kerninhalte
4. Erstelle Zusammenfassung, Themenübersicht und Lernziele

Die Analyse muss sich AUSSCHLIESSLICH auf die oben bereitgestellten Inhalte beziehen."""

        # Initialisiere das Modell OHNE Retrieval Tool (wir haben bereits die Inhalte)
        model = GenerativeModel(
            model_name='gemini-2.5-pro',
            system_instruction=SYSTEM_PROMPT
        )

        # Generiere den Inhalt
        response = model.generate_content(user_prompt)

        # Extrahiere den Text sicher (auch bei mehreren Parts)
        full_text = ""
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if part.text:
                    full_text += part.text

        # Quellen sind jetzt die Chunks, die wir explizit gesucht haben
        used_sources = [f"gs://{GCS_BUCKET_NAME}/{actual_filename}"]

        return jsonify({
            "analysis_result": full_text,
            "used_sources": used_sources,
            "chunks_found": len(document_chunks)
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
    logger.info(f"Using direct Discovery Engine search API (no generic retrieval tool)")
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
