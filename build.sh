#!/bin/bash
# Build script for Flink Job Runner

set -e

# Configuration
IMAGE_NAME="${IMAGE_NAME:-flink-job-runner}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
REGISTRY="${REGISTRY:-}"

# Add registry prefix if provided
if [ -n "$REGISTRY" ]; then
    FULL_IMAGE_NAME="$REGISTRY/$IMAGE_NAME:$IMAGE_TAG"
else
    FULL_IMAGE_NAME="$IMAGE_NAME:$IMAGE_TAG"
fi

echo "Building Docker image: $FULL_IMAGE_NAME"

# Build the Docker image
docker build -t "$FULL_IMAGE_NAME" .

echo "✅ Docker image built successfully"

# Push if registry is specified
if [ -n "$REGISTRY" ]; then
    echo "Pushing to registry: $REGISTRY"
    docker push "$FULL_IMAGE_NAME"
    echo "✅ Image pushed successfully"
fi

echo "🎉 Build completed!"
echo "Image: $FULL_IMAGE_NAME"
echo ""
echo "To run locally:"
echo "docker run -p 8080:8080 $FULL_IMAGE_NAME"
echo ""
echo "To deploy to Kubernetes, update k8s-deployment.yaml with your image and run:"
echo "kubectl apply -f k8s-deployment.yaml"