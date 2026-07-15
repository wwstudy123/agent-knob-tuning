#!/bin/bash
set -e

python "./workload analyzer/WorkloadParser.py"
python "./knob selector/anonymize.py"
python "./knob selector/knob_select.py"
python "./range pruner/anonymize.py"
python "./range pruner/range_pruner.py"

# Start LLM server in background
python "./configuration recommender/LLM_server.py" &
LLM_PID=$!
trap "kill $LLM_PID 2>/dev/null" EXIT

# Wait for server to be ready
echo "Waiting for LLM server to start..."
for i in $(seq 1 30); do
    if curl -s http://127.0.0.1:4000/ > /dev/null 2>&1; then
        echo "LLM server is ready."
        break
    fi
    sleep 1
done

python "./configuration recommender/DB_client.py"