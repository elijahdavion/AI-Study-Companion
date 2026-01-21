# Use an official Python runtime as a parent image
FROM python:3.9-slim-buster

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy your application code (app.py and indexer.py)
# Both are copied into the same container for the main service
COPY app.py .
COPY indexer.py .

# Assuming your frontend HTML is in a folder within your project root
# IMPORTANT: Replace 'YOUR_FRONTEND_FOLDER_NAME' with the actual name of your frontend folder.
# For example, if your frontend HTML is in a folder called 'web', you would put:
# COPY web ./web
COPY YOUR_FRONTEND_FOLDER_NAME ./YOUR_FRONTEND_FOLDER_NAME

# Command to run the application using gunicorn
# This tells gunicorn to look for an 'app' object within 'app.py'
# Your web service (app.py) will listen on port 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]

