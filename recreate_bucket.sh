#!/bin/bash
# This script deletes the GCS bucket and recreates it in the correct region.

# Exit immediately if a command exits with a non-zero status.
set -e

# Your GCS bucket name
BUCKET_NAME="ai-study-companion-bucket"

# The desired region for your bucket
REGION="europe-west1"

# 1. Delete all objects in the bucket (gsutil requires the bucket to be empty).
#    The -f flag forces the operation without interactive confirmation.
echo "Attempting to empty the bucket: $BUCKET_NAME..."
gsutil -m rm -f "gs://$BUCKET_NAME/**" || echo "Bucket is already empty or does not exist."

# 2. Delete the bucket.
echo "Attempting to delete the bucket: $BUCKET_NAME..."
gsutil rb "gs://$BUCKET_NAME" || echo "Bucket does not exist."

# 3. Create the new bucket in the specified region.
echo "Creating new bucket: $BUCKET_NAME in region: $REGION..."
gsutil mb -l "$REGION" "gs://$BUCKET_NAME"

echo "Bucket recreation complete."
