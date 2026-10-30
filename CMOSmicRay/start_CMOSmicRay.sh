#!/bin/bash
cd "$(dirname "$0")"

# --- CONFIGURATION CHECK ---
# All credentials and settings live in .env (see .env.example).
# CMOSmicRay.py loads .env automatically via python-dotenv.
if [ ! -f .env ]; then
    echo "[$(date)] ERROR: .env file not found."
    echo "Copy .env.example to .env and fill in your CREDO credentials and location."
    exit 1
fi

# --- PROCESS MANAGEMENT ---
if pgrep -f "CMOSmicRay.py" > /dev/null
then
    echo "[$(date)] CMOSmicRay is already running."
else
    echo "[$(date)] Initializing CMOSmicRay..."

    # Launch the detector in the background
    nohup python3 CMOSmicRay.py >> CMOSmicRay.log 2>&1 &

    # Launch the Dashboard Web Server in the background.
    # Scoped to public/ so .env, CMOSmicRay.py, and other project files are
    # never reachable over HTTP -- only the dashboard and its JSON data files.
    if ! pgrep -f "http.server 8000" > /dev/null
    then
        nohup python3 -m http.server 8000 --directory public >> dashboard.log 2>&1 &
        echo "[$(date)] Dashboard Server launched on port 8000 (serving public/ only)."
    fi

    echo "[$(date)] Process launched."
fi
