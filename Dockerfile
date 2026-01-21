# Use an official Python runtime as a parent image
FROM python:3.9-slim-buster

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy your application code (app.py and indexer.py)
# Both are copied into this container. app.py will be run by gunicorn,
# and indexer.py will be available for event-driven triggers (if this service handles them).
COPY app.py .
COPY indexer.py .

# Copy your frontend folder into the container
# Based on your 'ls' output, your frontend folder is named 'frontend'
COPY frontend ./frontend

# Command to run the application using gunicorn
# This tells gunicorn to look for an 'app' object within 'app.py'
# Your web service (app.py) will listen on port 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
