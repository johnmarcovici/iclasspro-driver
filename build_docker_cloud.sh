#!/bin/bash
# Script to build and push iClassPro Dashboard container image to Google Cloud
# Container runtime agnostic - supports docker, podman, and others
#
# Container Runtime Detection:
#   - Respects CONTAINER_CLI environment variable for override
#   - Auto-detects docker, podman (any OCI-compatible builder)
#   - Pushes to Google Container Registry (gcr.io)
#
# Examples:
#   ./build_docker_cloud.sh                  # Auto-detect
#   CONTAINER_CLI=podman ./build_docker_cloud.sh  # Force podman
#   CONTAINER_CLI=docker ./build_docker_cloud.sh  # Force docker

set -e

cd "$(dirname "$0")"

echo "================================================"
echo "iClassPro Dashboard - Cloud Build & Deploy"
echo "================================================"
echo ""

# Detect container runtime
if [ -n "$CONTAINER_CLI" ]; then
    CONTAINER_CMD="$CONTAINER_CLI"
else
    if command -v docker &> /dev/null; then
        CONTAINER_CMD="docker"
    elif command -v podman &> /dev/null; then
        CONTAINER_CMD="podman"
    else
        echo "❌ Error: No container runtime found."
        echo "   Install one of:"
        echo "   • Docker - https://www.docker.com/products/docker-desktop"
        echo "   • Podman - https://podman.io/docs/installation"
        exit 1
    fi
fi

if ! command -v gcloud &> /dev/null; then
    echo "❌ Error: Google Cloud CLI (gcloud) is not installed."
    echo "   Install from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

echo "✅ Using container runtime: $CONTAINER_CMD"
echo "✅ gcloud CLI is installed"
echo ""

# Get current project
CURRENT_PROJECT=$(gcloud config get-value project 2>/dev/null)

if [ -z "$CURRENT_PROJECT" ]; then
    echo "❌ Error: No Google Cloud project is configured."
    echo "   Set one with: gcloud config set project YOUR_PROJECT_ID"
    exit 1
fi

echo "Current Google Cloud Project: $CURRENT_PROJECT"
echo ""

# Confirm project
read -p "Use this project for deployment? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Please configure the correct project with:"
    echo "  gcloud config set project YOUR_PROJECT_ID"
    exit 0
fi

PROJECT_ID=$CURRENT_PROJECT
IMAGE_NAME="iclasspro-dashboard"
IMAGE_TAG="latest"
REGISTRY_URL="gcr.io/$PROJECT_ID/$IMAGE_NAME:$IMAGE_TAG"

echo ""
echo "Build Configuration:"
echo "  Project:      $PROJECT_ID"
echo "  Image:        $IMAGE_NAME"
echo "  Tag:          $IMAGE_TAG"
echo "  Registry URL: $REGISTRY_URL"
echo ""

# Configure authentication
echo "🔐 Configuring container authentication with Google Cloud..."
gcloud auth configure-docker

echo ""
echo "🔨 Building container image..."
echo "   (This may take a few minutes on first build)"
$CONTAINER_CMD build -t $REGISTRY_URL .

if [ $? -ne 0 ]; then
    echo "❌ Build failed. Check the error above."
    exit 1
fi

echo ""
echo "✅ Container image built successfully!"
echo ""

# Confirm push
read -p "Push image to Google Container Registry? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "ℹ️  Image is built locally but not pushed."
    echo "   Push later with: $CONTAINER_CMD push $REGISTRY_URL"
    exit 0
fi

echo ""
echo "📤 Pushing image to Google Container Registry..."
$CONTAINER_CMD push $REGISTRY_URL

if [ $? -ne 0 ]; then
    echo "❌ Push failed. Check your credentials and network."
    exit 1
fi

echo ""
echo "✅ Image pushed successfully!"
echo ""
echo "================================================"
echo "Next steps:"
echo ""
echo "1. Configure Cloud SQL (PostgreSQL):"
echo "   See CLOUD_DEPLOYMENT.md - 'Step 1: Create Cloud SQL Database'"
echo ""
echo "2. Set up secrets in Secret Manager:"
echo "   See CLOUD_DEPLOYMENT.md - 'Step 2: Configure Secrets'"
echo ""
echo "3. Deploy to Cloud Run:"
echo "   See CLOUD_DEPLOYMENT.md - 'Step 4: Deploy to Cloud Run'"
echo ""
echo "Or run this command:"
echo "  gcloud run deploy iclasspro-dashboard \\"
echo "    --image $REGISTRY_URL \\"
echo "    --region us-central1 \\"
echo "    --platform managed"
echo ""
echo "================================================"
