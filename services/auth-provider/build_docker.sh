#!/bin/bash
# Simple script to build the Auth Provider Docker image
IMAGE_NAME="therefore-auth-provider"
TAG="latest"

echo "Building ${IMAGE_NAME}:${TAG}..."
docker build -t ${IMAGE_NAME}:${TAG} .
echo "Done!"
