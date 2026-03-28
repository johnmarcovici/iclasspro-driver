#!/bin/bash
# Show app logs for local multi-user environment

set -e
cd "$(dirname "$0")"

REQUIRE_DOCKER=1 source prepare_env.sh

run_docker_compose logs -f app
