import base64
import json
import os
import hashlib
from flask import Flask, request
from google.cloud import discoveryengine_v1 as discoveryengine
from google.api_core.client_options import ClientOptions

app = Flask(__name__)

@app.route('/', methods=['POST'])
def index():
    envelope = request.get_json()
    if not envelope or 'message' not in envelope:
        print('Not a valid Pub/Sub message')
        return 'Bad Request: Invalid Pub/Sub message format', 400

    message = envelope['message']
    if 'data' not in message:
        print('No data in Pub/Sub message')
        return 'Bad Request: No data in message', 400

    try:
        data = json.loads(base64.b64decode(message['data']).decode('utf-8'))
        bucket = data.get('bucket')
        name = data.get('name')
    except Exception as e:
        print(f'Error decoding message data: {e}')
        return 'Bad Request: Invalid message data', 400

    if not bucket or not name:
        print(f'Missing bucket or name in GCS event data: {data}')
        return 'Bad Request: Missing GCS object details', 400

    gcs_uri = f'gs://{bucket}/{name}'
    print(f'Processing file: {gcs_uri}')

    project_id = os.environ.get('GCP_PROJECT_ID')
    data_store_id = os.environ.get('DATA_STORE_ID')
    location = os.environ.get('DATA_STORE_LOCATION', 'eu')

    if not project_id or not data_store_id:
        print('Error: Missing GCP_PROJECT_ID or DATA_STORE_ID environment variables')
        return 'Internal Server Error: Configuration missing', 500

    try:
        # Regionaler Endpoint für EU
        client_options = None
        if location and location != 'global':
            api_endpoint = f"{location}-discoveryengine.googleapis.com"
            client_options = ClientOptions(api_endpoint=api_endpoint)
            print(f"Using Regional API Endpoint: {api_endpoint} (REST)")

        client = discoveryengine.DocumentServiceClient(
            client_options=client_options,
            transport="rest"
        )

        # SICHERER PFAD FÜR EU: 
        # Wir bauen den Pfad manuell mit 'branches/0', da 'default_branch' in EU oft hakt.
        parent = f"projects/{project_id}/locations/{location}/collections/default_collection/dataStores/{data_store_id}/branches/0"

        # ID generieren (Hash)
        doc_id = hashlib.md5(name.encode('utf-8')).hexdigest()

        # Dokument Objekt ohne das fehlerhafte 'parent' Feld
        document = discoveryengine.Document(
            id=doc_id,
            content=discoveryengine.Document.Content(
                uri=gcs_uri,
                mime_type='application/pdf'
            )
        )

        request_body = discoveryengine.ImportDocumentsRequest(
            parent=parent,
            inline_source=discoveryengine.ImportDocumentsRequest.InlineSource(
                documents=[document]
            ),
            reconciliation_mode=discoveryengine.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL
        )

        operation = client.import_documents(request=request_body)
        print(f'Started document import operation: {operation.operation.name}')
        
    except Exception as e:
        print(f'Error during Discovery Engine import: {e}')
        return f'Internal Server Error: {e}', 500

    return 'OK', 202

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=True, host='0.0.0.0', port=port)