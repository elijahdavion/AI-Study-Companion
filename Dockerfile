FROM python:3.11-slim

# Setze Arbeitsverzeichnis
WORKDIR /app

# Kopiere requirements und installiere Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiere Anwendungscode und Templates
COPY app.py .
COPY templates/ ./templates/

# Exponiere Port (Cloud Run nutzt PORT env var)
EXPOSE 8080

# Starte Anwendung mit Gunicorn
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 --log-level debug app:app
