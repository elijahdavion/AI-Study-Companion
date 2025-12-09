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
DATA_STORE_LOCATION = "eu"  # Location des Data Store
DATA_STORE_ID_SHORT = "ai-study-companion-data-store_1765190826355"  # Nur die ID
tools = []
if PROJECT_ID:
    # Definiere das Retrieval-Tool für Vertex AI Search
    datastore_tool = Tool.from_retrieval(
        retrieval=grounding.Retrieval(
            source=grounding.VertexAISearch(
                data_store_id=DATA_STORE_ID_SHORT,
                project=PROJECT_ID,
                location=DATA_STORE_LOCATION
            )
        )
    )
    tools = [datastore_tool]

# --- Spezifischer Prompt (System Instruction) ---
SYSTEM_PROMPT = """
Sie sind ein hochspezialisierter KI-Studienbegleiter. Ihre Aufgabe ist es, die bereitgestellte Dokumentation (das PDF-Skript) zu analysieren und eine strukturierte Markdown-Antwort zu generieren.

Nutzen Sie das bereitgestellte "Data Store" Tool, um Informationen aus dem Skript abzurufen.

Die Antwort muss exakt DREI spezifische Abschnitte enthalten:

1.  **Umfassende Zusammenfassung (Summary):** Eine prägnante, aber vollständige Zusammenfassung der wichtigsten Konzepte und Argumente.
2.  **Thematische Übersicht (Topics):** Eine hierarchische (nummerierte oder verschachtelte) Gliederung der im Skript behandelten Hauptthemen und Unterpunkte.
3.  **Lernziele (Learning Goals):** Eine Liste von mindestens fünf spezifischen, messbaren Lernzielen (SMART-Prinzip) in Form von Aktionsverben ("Der Studierende kann...", "Definieren Sie...", "Analysieren Sie...").

Die Antwort MUSS ausschließlich im Markdown-Format erfolgen.
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
        file_name = data.get("file_name") # z.B. 'gcs://your-bucket-name/skript.pdf'

        if not file_name:
            return jsonify({"error": "Fehlendes 'file_name' im JSON-Body."}), 400
        
        # Der Prompt muss das Modell anweisen, das Tool zu benutzen
        user_prompt = f"Analysiere das Dokument '{file_name}'. Nutze UNBEDINGT das verfügbare Data Store Tool um den Inhalt zu finden. Erstelle dann: 1) Zusammenfassung, 2) Thematische Übersicht, 3) Lernziele."

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
