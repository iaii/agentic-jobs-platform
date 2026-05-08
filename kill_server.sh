#!/bin/bash

# Kill all running instances of the agentic jobs server (uvicorn on port 8000 and any stray processes)

echo "Killing agentic jobs server processes..."

KILLED=0

# Kill anything on port 8000
PORT_PIDS=$(lsof -ti :8000 2>/dev/null)
if [ -n "$PORT_PIDS" ]; then
    echo "  Killing port 8000 PIDs: $PORT_PIDS"
    kill -9 $PORT_PIDS 2>/dev/null
    KILLED=$((KILLED + $(echo "$PORT_PIDS" | wc -w | tr -d ' ')))
fi

# Kill any uvicorn process referencing agentic_jobs or backend.main
UVICORN_PIDS=$(ps aux | grep -E "uvicorn.*(agentic_jobs|backend\.main)" | grep -v grep | awk '{print $2}')
if [ -n "$UVICORN_PIDS" ]; then
    echo "  Killing uvicorn PIDs: $UVICORN_PIDS"
    kill -9 $UVICORN_PIDS 2>/dev/null
    KILLED=$((KILLED + $(echo "$UVICORN_PIDS" | wc -w | tr -d ' ')))
fi

if [ "$KILLED" -eq 0 ]; then
    echo "  Nothing to kill — server was not running."
else
    echo "  Done. Killed $KILLED process(es)."
fi
