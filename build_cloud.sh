#!/bin/bash
# Build and push iClassPro Dashboard image to Google Cloud Run

set -e
cd "$(dirname "$0")"

# Require Docker and gcloud
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is required"
    echo "   Download: https://www.docker.com/products/docker-desktop"
    exit 1
fi

if ! command -v gcloud &> /dev/null; then
    echo "❌ Error: gcloud CLI is required"
    echo "   Install: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

echo "================================================"
echo "iClassPro Dashboard - Build for Cloud"
echo "================================================"
echo ""

# Get and confirm project
PROJECT=$(gcloud config get-value project 2>/dev/null)
[ -z "$PROJECT" ] && {
    echo "❌ No GCP project configured"
    echo "   Run: gcloud config set project YOUR_PROJECT_ID"
    exit 1
}

echo "Project: $PROJECT"
read -p "Proceed? (y/n) " -n 1 -r
echo
[[ ! $REPLY =~ ^[Yy]$ ]] && exit 0

REGISTRY="gcr.io/$PROJECT/iclasspro-dashboard:latest"

echo ""
echo "🔐 Configuring Docker auth..."
gcloud auth configure-docker

echo "🔨 Building image..."
docker build -t $REGISTRY .

echo "✅ Built successfully"
read -p "Push to registry? (y/n) " -n 1 -r
echo
[[ ! $REPLY =~ ^[Yy]$ ]] && exit 0

echo "📤 Pushing..."
docker push $REGISTRY

echo ""
echo "✅ Pushed: $REGISTRY"
echo ""
echo "Next: See CLOUD_DEPLOYMENT.md for deployment steps"
