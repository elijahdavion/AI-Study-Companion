import os
from flask import Flask, request, jsonify, render_template
import vertexai
from vertexai.generative_models import GenerativeModel, Tool, grounding

# --- Konfiguration ---
PROJECT_ID = os.getenv("GCP_PROJECT_ID") 
REGION = "europe-west1" 
DATA_STORE_ID = os.getenv("DATA_STORE_ID")

# Initialisiere Vertex AI
if PROJECT_ID:
    vertexai.init(project=PROJECT_ID, location=REGION)

# --- Tool Definition für Data Store ---
# DATA_STORE_LOCATION = "eu"  # Location des Data Store
# DATA_STORE_ID_SHORT = "ai-study-companion-data-store_1765190826355"  # Nur die ID
tools = []
if PROJECT_ID:
    # Definiere das Retrieval-Tool für Vertex AI Search
    datastore_tool = Tool.from_retrieval(
        retrieval=grounding.Retrieval(
            source=grounding.VertexAISearch(
                datastore=DATA_STORE_ID,
                # project=PROJECT_ID,
                #location=DATA_STORE_LOCATION
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
2.  **Thematische Übersicht:** Eine hierarchische (nummerierte oder verschachtelte) Gliederung der im Skript behandelten Hauptthemen und Unterpunkte.
3.  **Lernziele:** Eine Liste von mindestens fünf spezifischen, messbaren Lernzielen (SMART-Prinzip) in Form von Aktionsverben ("Der Studierende kann...", "Definieren Sie...", "Analysieren Sie...").

Die Antwort MUSS ausschließlich im Markdown-Format erfolgen.

Bitte lasse Voworte raus wie: Gerne fasse ich die wichtigsten Inhalte des vorliegenden Skripts zusammen. Die bereitgestellten Informationen behandeln grundlegende Aspekte der Bildaufnahme und Videosignalübertragung.

Gebe nur oben genannten 3 Punkte an.
"""

app = Flask(__name__)

@app.route("/")
def home():
    """
    Startseite mit Web-Interface.
    """
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze_script():
    """
    HTTP-Endpunkt, der eine Datei analysiert und strukturierte Ergebnisse liefert.
    """
    try:
        if not PROJECT_ID or not DATA_STORE_ID:
             return jsonify({"error": "Server misconfiguration: Missing GCP_PROJECT_ID or DATA_STORE_ID"}), 500

        data = request.get_json()
        file_name = data.get("file_name") # 'gcs://ai-study-companion/BWS.pdf'

        if not file_name:
            return jsonify({"error": "Fehlendes 'file_name' im JSON-Body."}), 400
        
        # Der Prompt muss das Modell anweisen, das Tool zu benutzen
        user_prompt = "Fasse die wichtigsten Inhalte des vorliegenden Skripts zusammen. Nutze UNBEDINGT das verfügbare Data Store Tool, um alle relevanten Fakten abzurufen. Erstelle dann die drei geforderten Abschnitte (Zusammenfassung, Themenübersicht, Lernziele)."

        # Initialisiere das Modell mit den Tools
        model = GenerativeModel(
            model_name='gemini-2.5-flash',
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
