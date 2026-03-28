#!/bin/bash
# Quick reference for running iClassPro Dashboard

cat << 'EOF'
╔════════════════════════════════════════════════════════════════════╗
║          iClassPro Dashboard - Quick Start Guide                   ║
╚════════════════════════════════════════════════════════════════════╝

Choose your setup below:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1️⃣  SIMPLE SINGLE-USER (Default)

  ./run_dashboard.sh

  • Best for: Testing on your laptop
  • Access: http://localhost:8000
  • Database: SQLite (.env)
  • Setup time: < 1 minute
  • Requirements: Python 3, Playwright

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

2️⃣  MULTI-USER LOCAL (PostgreSQL + Docker)

  ./run_local.sh

  • Best for: Multi-user testing, real database experience
  • Access: http://localhost:8000
  • Database: PostgreSQL (containerized)
  • Admin UI: http://localhost:5050
  • Setup time: 1-2 minutes
  • Requirements: Docker & docker-compose

  To stop:
  ./stop_local.sh

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

3️⃣  CLOUD DEPLOYMENT (Google Cloud Run)

  ./build_cloud.sh

  • Best for: Production, always-on, multi-user cloud hosting
  • Access: Your custom domain (e.g., enrollment.example.com)
  • Database: Google Cloud SQL
  • Setup time: 10-20 minutes
  • Requirements: Google Cloud account, gcloud CLI, Docker

  Then follow the deployment steps in CLOUD_DEPLOYMENT.md

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMPARISON TABLE:

  Feature           │ Single-User │ Multi-User Local │ Cloud
  ─────────────────┼─────────────┼──────────────────┼──────────
  Login required    │ Yes         │ Yes              │ Yes
  Credentials saved │ Encrypted   │ Encrypted        │ Encrypted
  Job history       │ Yes         │ Yes              │ Yes
  Concurrent users  │ 1           │ Multi            │ Multi
  Database          │ SQLite      │ PostgreSQL       │ Cloud SQL
  Cost              │ Free        │ Free             │ $$$
  Deployment        │ Localhost   │ Localhost        │ Cloud
  Scalability       │ Single PC   │ Single PC        │ Unlimited

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RECOMMENDATIONS:

  👤 Personal use (1 user):
     → ./run_dashboard.sh

  👥 Team testing (2-5 users):
     → ./run_docker_local.sh

  🌍 Production deployment:
     → ./build_docker_cloud.sh

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Questions? See:
  • README.md              - Full user guide
  • BUILD_SUMMARY.md       - Technical implementation details
  • CLOUD_DEPLOYMENT.md    - Step-by-step cloud guide

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CONTAINER RUNTIME SUPPORT:
REQUIREMENTS:

For local & cloud deployments, you need:
  • Docker Desktop: https://www.docker.com/products/docker-desktop
  • For cloud: Google Cloud SDK: https://cloud.google.com/sdk/docs/instal
To override detection, set CONTAINER_CLI:
  CONTAINER_CLI=podman ./run_docker_local.sh
  CONTAINER_CLI=docker ./build_docker_cloud.sh

EOF
