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

2️⃣  MULTI-USER LOCAL (PostgreSQL + Containers)

  ./run_docker_local.sh

  • Best for: Multi-user testing, real database experience
  • Access: http://localhost:8000
  • Database: PostgreSQL (containerized)
  • Admin UI: http://localhost:5050
  • Setup time: 1-2 minutes
  • Container runtime: Auto-detects docker/podman/podman-compose
  • Requirements: Docker OR Podman

  To stop:
  ./stop_docker_local.sh

  Note: Set CONTAINER_CLI=podman or CONTAINER_CLI=docker to override detection

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

3️⃣  CLOUD DEPLOYMENT (Google Cloud Run)

  ./build_docker_cloud.sh

  • Best for: Production, always-on, multi-user cloud hosting
  • Access: Your custom domain (e.g., enrollment.example.com)
  • Database: Google Cloud SQL
  • Setup time: 10-20 minutes
  • Container runtime: Auto-detects docker/podman
  • Requirements: Google Cloud account, gcloud CLI, Docker OR Podman

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

The scripts auto-detect your container runtime. Supported:
  • docker & docker-compose
  • podman & podman-compose
  • Any OCI-compatible container tool

To override detection, set CONTAINER_CLI:
  CONTAINER_CLI=podman ./run_docker_local.sh
  CONTAINER_CLI=docker ./build_docker_cloud.sh

EOF
