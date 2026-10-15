#!/bin/bash
# Monitor experiment progress

echo "=========================================="
echo "P1.3 Verification Experiment Monitor"
echo "=========================================="
echo ""

RECORD_DIR="configuration recommender/record-p1-verify"

while true; do
    clear
    echo "=========================================="
    echo "P1.3 Verification Experiment Monitor"
    echo "=========================================="
    echo ""
    echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""

    # Check if checkpoint exists
    if [ -f "$RECORD_DIR/checkpoint.json" ]; then
        echo "✅ Checkpoint found"

        # Extract iteration and status
        ITERATION=$(python3 -c "import json; ckpt=json.load(open('$RECORD_DIR/checkpoint.json')); print(ckpt.get('iteration', 'N/A'))" 2>/dev/null)
        STATUS=$(python3 -c "import json; ckpt=json.load(open('$RECORD_DIR/checkpoint.json')); print(ckpt.get('status', 'N/A'))" 2>/dev/null)

        echo "   Iteration: $ITERATION"
        echo "   Status: $STATUS"
        echo ""

        # Check if search_space is in checkpoint
        HAS_SEARCH_SPACE=$(python3 -c "import json; ckpt=json.load(open('$RECORD_DIR/checkpoint.json')); print('YES' if 'search_space' in ckpt else 'NO')" 2>/dev/null)
        echo "   SearchSpace in checkpoint: $HAS_SEARCH_SPACE"
        echo ""
    else
        echo "⏳ Waiting for checkpoint..."
        echo ""
    fi

    # Check decision log
    if [ -f "$RECORD_DIR/decision_log.jsonl" ]; then
        DECISION_COUNT=$(wc -l < "$RECORD_DIR/decision_log.jsonl")
        echo "✅ Decision log: $DECISION_COUNT decisions"

        # Show last decision
        if [ $DECISION_COUNT -gt 0 ]; then
            LAST_DECISION=$(tail -1 "$RECORD_DIR/decision_log.jsonl")
            DECISION_NAME=$(echo "$LAST_DECISION" | python3 -c "import sys, json; print(json.loads(sys.stdin.read()).get('name', 'N/A'))" 2>/dev/null)
            echo "   Last decision: $DECISION_NAME"
        fi
        echo ""
    else
        echo "⏳ Waiting for decision log..."
        echo ""
    fi

    # Check turn files
    TURN_COUNT=$(ls -1 "$RECORD_DIR"/turn_* 2>/dev/null | wc -l)
    echo "✅ Turn files: $TURN_COUNT"
    echo ""

    # Check if experiment is complete
    if [ -f "$RECORD_DIR/checkpoint.json" ]; then
        STATUS=$(python3 -c "import json; ckpt=json.load(open('$RECORD_DIR/checkpoint.json')); print(ckpt.get('status', 'running'))" 2>/dev/null)
        if [ "$STATUS" = "completed" ]; then
            echo "=========================================="
            echo "✅ EXPERIMENT COMPLETED!"
            echo "=========================================="
            echo ""
            echo "Run verification:"
            echo "  python verify_p1_3.py"
            echo ""
            break
        fi
    fi

    echo "Press Ctrl+C to exit monitor"
    sleep 10
done
