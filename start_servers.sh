#!/bin/bash
# Script to bring up ApplyPilot servers (Dashboard and AI Fill Server)

# Get the directory of this script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$DIR"

# Path to python virtual environment executable
PYTHON_EXE=".venv/bin/python"
APPLYPILOT_EXE=".venv/bin/applypilot"

if [ ! -f "$APPLYPILOT_EXE" ]; then
    echo "Error: Virtual environment or applypilot executable not found at $APPLYPILOT_EXE"
    echo "Please ensure you have run setup/installation."
    exit 1
fi

DASHBOARD_PORT=8089
SERVE_PORT=8088
DASHBOARD_LOG="dashboard.log"
SERVE_LOG="serve.log"

stop_servers() {
    echo "Stopping any running ApplyPilot servers..."
    
    # Find and kill dashboard server running on DASHBOARD_PORT
    DASHBOARD_PID=$(lsof -t -i:$DASHBOARD_PORT 2>/dev/null)
    if [ ! -z "$DASHBOARD_PID" ]; then
        echo "Killing Dashboard server (PID: $DASHBOARD_PID)"
        kill $DASHBOARD_PID 2>/dev/null
    fi
    
    # Find and kill AI fill server running on SERVE_PORT
    SERVE_PID=$(lsof -t -i:$SERVE_PORT 2>/dev/null)
    if [ ! -z "$SERVE_PID" ]; then
        echo "Killing AI Fill server (PID: $SERVE_PID)"
        kill $SERVE_PID 2>/dev/null
    fi
    
    # Also find any stray applypilot processes
    pkill -f "applypilot dashboard" 2>/dev/null
    pkill -f "applypilot serve" 2>/dev/null
    
    echo "Servers stopped."
}

status_servers() {
    DASHBOARD_PID=$(lsof -t -i:$DASHBOARD_PORT 2>/dev/null)
    SERVE_PID=$(lsof -t -i:$SERVE_PORT 2>/dev/null)
    
    if [ ! -z "$DASHBOARD_PID" ]; then
        echo "Dashboard Server: RUNNING (PID: $DASHBOARD_PID) on http://localhost:$DASHBOARD_PORT"
    else
        echo "Dashboard Server: STOPPED"
    fi
    
    if [ ! -z "$SERVE_PID" ]; then
        echo "AI Fill Server:   RUNNING (PID: $SERVE_PID) on http://localhost:$SERVE_PORT"
    else
        echo "AI Fill Server:   STOPPED"
    fi
}

start_servers() {
    # Stop existing servers first to avoid port conflicts
    stop_servers
    
    echo "Starting ApplyPilot Dashboard Server on port $DASHBOARD_PORT..."
    nohup "$APPLYPILOT_EXE" dashboard --port $DASHBOARD_PORT --no-open > "$DASHBOARD_LOG" 2>&1 &
    DASHBOARD_PID=$!
    
    echo "Starting ApplyPilot AI Fill Server on port $SERVE_PORT..."
    nohup "$APPLYPILOT_EXE" serve --port $SERVE_PORT > "$SERVE_LOG" 2>&1 &
    SERVE_PID=$!
    
    # Give them a moment to start
    sleep 2
    
    # Check if they are actually running
    if ps -p $DASHBOARD_PID > /dev/null; then
        echo "Dashboard server successfully started (PID: $DASHBOARD_PID) - logs written to $DASHBOARD_LOG"
        echo "Access Dashboard at: http://localhost:$DASHBOARD_PORT"
    else
        echo "Error: Dashboard server failed to start. Check $DASHBOARD_LOG for details."
    fi
    
    if ps -p $SERVE_PID > /dev/null; then
        echo "AI Fill server successfully started (PID: $SERVE_PID) - logs written to $SERVE_LOG"
        echo "AI Fill API running at: http://localhost:$SERVE_PORT"
    else
        echo "Error: AI Fill server failed to start. Check $SERVE_LOG for details."
    fi
}

case "$1" in
    start)
        start_servers
        ;;
    stop)
        stop_servers
        ;;
    status)
        status_servers
        ;;
    restart)
        stop_servers
        sleep 1
        start_servers
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        echo "Defaulting to 'start'..."
        start_servers
        ;;
esac
