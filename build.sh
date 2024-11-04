#!/bin/bash

# Exit immediately on error
set -e

# Define variables for the Docker Hub repository
DOCKERHUB_USERNAME="kg97"
REPOSITORY_NAME="saleor-api"

# Build the Docker image
echo "Building the Saleor Docker image..."
docker build -t "$DOCKERHUB_USERNAME/$REPOSITORY_NAME:latest" .

# Tag the image as "latest"
echo "Tagging the image as 'latest'..."
docker tag "$DOCKERHUB_USERNAME/$REPOSITORY_NAME:latest" "$DOCKERHUB_USERNAME/$REPOSITORY_NAME:latest"

# Push the image to Docker Hub
echo "Pushing the Docker image to Docker Hub..."
docker push "$DOCKERHUB_USERNAME/$REPOSITORY_NAME:latest"

echo "*****************************************************"
echo "*  Docker image built, tagged as 'latest', and pushed!   *"
echo "*****************************************************"
