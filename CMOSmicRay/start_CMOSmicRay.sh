#!/bin/bash
cd "$(dirname "$0")"

# --- CONFIGURATION ---
# Configuration and credentials are now loaded from the .env file in this directory.
# Ensure you copy .env.example to .env and fill in your details.

# --- PROCESS MANAGEMENT ---
# Check if the new production script is already running
if pgrep -f "CMOSmicRay.py" > /dev/null
then
    echo "[$(date)] CMOSmicRay (Production $CREDO_SOFTWARE_VERSION) is already running."
else
    echo "[$(date)] Initializing CMOSmicRay..."
    
    # Ensure dependencies are available
    echo "[$(date)] Verifying python dependencies..."
    if ! python3 -c "import websockets" &> /dev/null; then
        echo "[$(date)] Installing websockets..."
        sudo apt-get install -y python3-websockets || pip3 install websockets --break-system-packages
    fi
    if ! python3 -c "import dotenv" &> /dev/null; then
        echo "[$(date)] Installing python-dotenv..."
        sudo apt-get install -y python3-dotenv || pip3 install python-dotenv --break-system-packages
    fi
    
    # Launch the script in the background using the variables above
    nohup python3 -u CMOSmicRay.py >> CMOSmicRay.log 2>&1 &
    
    # Launch the Dashboard Web Server in the background
    if ! pgrep -f "http.server 8000" > /dev/null
    then
        nohup python3 -m http.server 8000 >> dashboard.log 2>&1 &
        echo "[$(date)] Dashboard Server launched on port 8000."
    fi
    
    echo "[$(date)] Process launched. Profile: $CREDO_CAMERA_MODEL"
fi
