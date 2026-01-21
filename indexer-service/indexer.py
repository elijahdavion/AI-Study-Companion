import base64
import json
import os
from flask import Flask, request
from google.cloud import discoveryengine_v1 as discoveryengine

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

    # Decode the GCS event notification from Pub/Sub
    data = json.loads(base64.b64decode(message['data']).decode('utf-8'))
    bucket = data.get('bucket')
    name = data.get('name')

    if not bucket or not name:
        print(f'Missing bucket or name in GCS event data: {data}')
        return 'Bad Request: Missing GCS object details', 400

    gcs_uri = f'gs://{bucket}/{name}'
    print(f'Received event for: {gcs_uri}')

    project_id = os.environ.get('GCP_PROJECT_ID')
    data_store_id = os.environ.get('DATA_STORE_ID')
    location = 'global'  # For Discovery Engine Data Store

    if not project_id or not data_store_id:
        print('Missing GCP_PROJECT_ID or DATA_STORE_ID environment variables')
        return 'Internal Server Error: Configuration missing', 500

    try:
        client = discoveryengine.DocumentServiceClient()
        parent = client.branch_path(
            project=project_id,
            location=location,
            data_store=data_store_id,
            branch='default_branch'
        )

        request_body = discoveryengine.ImportDocumentsRequest(
            parent=parent,
            gcs_source=discoveryengine.GcsSource(input_uris=[gcs_uri]),
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
    app.run(host='0.0.0.0', port=port)
