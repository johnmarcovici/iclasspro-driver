#!/bin/bash
# Example script for autonomous iClassPro enrollment
# Save this as run_enrollment.sh and make it executable with chmod +x run_enrollment.sh

# Credentials are loaded automatically from .env file
# Make sure to create .env with your credentials before running

# Change to the script directory
cd "$(dirname "$0")"

# Activate virtual environment
source venv/bin/activate

# Run the enrollment with any additional arguments passed to this script
echo "Starting iClassPro enrollment at $(date)"
python iclasspro.py "$@"

echo "Enrollment completed at $(date)"