#!/bin/bash
# Example script for autonomous iClassPro enrollment
# This script handles environment setup and runs the enrollment task.

# Change to the script directory
cd "$(dirname "$0")"

# Prepare environment (creates venv, installs dependencies)
source prepare_env.sh

# Run the enrollment with any additional arguments passed to this script
echo "Starting iClassPro enrollment at $(date)"
python3 iclasspro.py "$@"

echo "Enrollment completed at $(date)"
