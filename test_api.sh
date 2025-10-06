#!/bin/bash
# Example script to test the FlinkJobRunner service

echo "Testing FlinkJobRunner API"
echo ""

# Check if service is running
echo "1. Checking health endpoint..."
curl -s http://localhost:8000/health | python3 -m json.tool
echo ""

# Upload files and start a job
echo "2. Uploading files and starting job..."
JOB_ID=$(curl -s -X POST http://localhost:8000/jobs/upload \
  -F "files=@examples/Makefile" \
  -F "files=@examples/config.yaml" \
  -F "target=test" \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['job_id'])")

echo "Job ID: $JOB_ID"
echo ""

# Wait a moment
sleep 2

# Check job status
echo "3. Checking job status..."
curl -s http://localhost:8000/jobs/$JOB_ID | python3 -m json.tool
echo ""

# List all jobs
echo "4. Listing all jobs..."
curl -s http://localhost:8000/jobs | python3 -m json.tool
echo ""

echo "Test complete!"
echo "To stream logs: python3 example_client.py logs $JOB_ID"
