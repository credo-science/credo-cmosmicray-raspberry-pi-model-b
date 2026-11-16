#!/bin/bash

# Match only processes whose command line STARTS with python (^), so a shell or ssh
# session that merely mentions the file name is not mistaken for the detector.
DETECTOR_PATTERN='^python3? .*CMOSmicRay\.py'
DASHBOARD_PATTERN='^python3? -m http\.server 8000'

# Find the PID of the CMOSmicRay.py process
PID=$(pgrep -f "$DETECTOR_PATTERN")

if [ -z "$PID" ]
then
    echo "[$(date)] No active CMOSmicRay process found."
else
    echo "[$(date)] Terminating CMOSmicRay process $PID..."
    kill $PID
    
    # Wait a moment for it to release the camera handle
    sleep 2
    
    # Check if it's actually gone
    if pgrep -f "$DETECTOR_PATTERN" > /dev/null
    then
        echo "[$(date)] Process did not exit, forcing shutdown..."
        kill -9 $PID
    else
        echo "[$(date)] CMOSmicRay stopped successfully. Camera handle released."
    fi

    # Stop the Dashboard Web Server
    DASH_PID=$(pgrep -f "$DASHBOARD_PATTERN")
    if [ ! -z "$DASH_PID" ]
    then
        echo "[$(date)] Terminating Dashboard Server $DASH_PID..."
        kill $DASH_PID
    fi
fi
