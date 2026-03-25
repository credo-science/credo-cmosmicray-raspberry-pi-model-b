#!/bin/bash

# Find the PID of the CMOSmicRay.py process
PID=$(pgrep -f "CMOSmicRay.py")

if [ -z "$PID" ]
then
    echo "[$(date)] No active CMOSmicRay process found."
else
    echo "[$(date)] Terminating CMOSmicRay process $PID..."
    kill $PID
    
    # Wait a moment for it to release the camera handle
    sleep 2
    
    # Check if it's actually gone
    if pgrep -f "CMOSmicRay.py" > /dev/null
    then
        echo "[$(date)] Process did not exit, forcing shutdown..."
        kill -9 $PID
    else
        echo "[$(date)] CMOSmicRay stopped successfully. Camera handle released."
    fi

    # Stop the Dashboard Web Server
    DASH_PID=$(pgrep -f "http.server 8000")
    if [ ! -z "$DASH_PID" ]
    then
        echo "[$(date)] Terminating Dashboard Server $DASH_PID..."
        kill $DASH_PID
    fi
fi
