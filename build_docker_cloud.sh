#!/bin/bash
# Script to build and push iClassPro Dashboard Docker image to Google Cloud
# This prepares the application for cloud deployment

set -e

cd "$(dirname "$0")"

echo "================================================"
echo "iClassPro Dashboard - Cloud Build & Deploy"
echo "================================================"
echo ""

# Check prerequisites
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is not installed."
    echo "   Download from: https://www.docker.com/products/docker-desktop"
    exit 1
fi

if ! command -v gcloud &> /dev/null; then
    echo "❌ Error: Google Cloud CLI (gcloud) is not installed."
    echo "   Install from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

echo "✅ Docker is installed"
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

# Configure Docker authentication
echo "🔐 Configuring Docker authentication with Google Cloud..."
gcloud auth configure-docker

echo ""
echo "🔨 Building Docker image..."
echo "   (This may take a few minutes on first build)"
docker build -t $REGISTRY_URL .

if [ $? -ne 0 ]; then
    echo "❌ Build failed. Check the error above."
    exit 1
fi

echo ""
echo "✅ Docker image built successfully!"
echo ""

# Confirm push
read -p "Push image to Google Container Registry? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "ℹ️  Image is built locally but not pushed."
    echo "   Push later with: docker push $REGISTRY_URL"
    exit 0
fi

echo ""
echo "📤 Pushing image to Google Container Registry..."
docker push $REGISTRY_URL

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
