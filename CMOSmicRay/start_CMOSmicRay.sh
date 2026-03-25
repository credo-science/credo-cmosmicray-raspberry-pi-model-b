#!/bin/bash
cd "$(dirname "$0")"

# --- USER CREDENTIALS ---
# (Updated to match the credentials in your snippet)
export CREDO_USER="YourUsername"
export CREDO_PASS="YourPassword"
export CREDO_STATION="YourStationName"

# --- LOCATION CONFIGURATION ---
export CREDO_LAT="YourLatitude"
export CREDO_LNG="YourLongitude"
export CREDO_ALT="YourAltitude"

# --- CAMERA SELECTION ---
# Set this to "C270" or "C922" - The Python script automagically tunes the drivers
export CREDO_CAMERA_MODEL="C270"

# --- HARDWARE & SOFTWARE CONFIGURATION ---
export CREDO_DEVICE_MODEL="RPi Model B"
export CREDO_SOFTWARE_VERSION="v1.0"

# --- PROCESS MANAGEMENT ---
# Check if the new production script is already running
if pgrep -f "CMOSmicRay.py" > /dev/null
then
    echo "[$(date)] CMOSmicRay (Production $CREDO_SOFTWARE_VERSION) is already running."
else
    echo "[$(date)] Initializing CMOSmicRay..."
    
    # Note: Hardware tuning (Exposure/Gain) is now handled internally by the Python script.
    
    # Launch the script in the background using the variables above
    nohup python3 CMOSmicRay.py >> CMOSmicRay.log 2>&1 &
    
    # Launch the Dashboard Web Server in the background
    if ! pgrep -f "http.server 8000" > /dev/null
    then
        nohup python3 -m http.server 8000 >> dashboard.log 2>&1 &
        echo "[$(date)] Dashboard Server launched on port 8000."
    fi
    
    echo "[$(date)] Process launched. Profile: $CREDO_CAMERA_MODEL"
fi
