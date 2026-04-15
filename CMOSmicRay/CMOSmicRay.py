#!/usr/bin/env python3
"""
Author: Jake Lee (Sole Developer)
Hardware: Raspberry Pi Model B | Logitech C270 / C922
Target: CREDO API v2 + Local Dashboard

"""

import cv2
import numpy as np
import time
import json
import base64
import requests
import os
import subprocess
import threading
import sys
import logging
import logging.handlers
import copy
from datetime import datetime
import asyncio
import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from collections import deque
from dotenv import load_dotenv

# Load local environment variables if .env exists
load_dotenv()

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
# Suppress noisy library logs to avoid confusion with CREDO connection
logging.getLogger("websockets").setLevel(logging.WARNING)

# --- CONFIGURATION ---
STATION_ID = os.getenv("CREDO_STATION") 
CREDO_USER = os.getenv("CREDO_USER")
DEVICE_MODEL = os.getenv("CREDO_DEVICE_MODEL")
SOFTWARE_VERSION = os.getenv("CREDO_SOFTWARE_VERSION")
CREDO_PASS = os.getenv("CREDO_PASS")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_LOG_FILE = os.path.join(BASE_DIR, "detections.json") 
LOCAL_STATUS_FILE = os.path.join(BASE_DIR, "status.json")

# --- HARDWARE PROFILES ---
# threshold      : legacy absolute pixel-diff gate (kept for reference)
# min_absolute   : hard minimum diff value to even consider a hit
# ratio_threshold: adaptive gate — maxVal must be >= ratio_threshold * bg_brightness
# ratio_min      : floor for the adaptive gate (ignores very dark backgrounds)
HARDWARE_PROFILES = {
    "C270_FAMILY": {
        "threshold": 60, "exposure": 1000, "gain": 255, "driver_quirk": "legacy",
        "min_absolute": 60, "ratio_threshold": 3.5, "ratio_min": 45
    },
    "C922_FAMILY": {
        "threshold": 95, "exposure": 150, "gain": 128, "driver_quirk": "standard",
        "min_absolute": 95, "ratio_threshold": 4.0, "ratio_min": 70
    },
    "GENERIC": {
        "threshold": 80, "exposure": 500, "gain": 150, "driver_quirk": "standard",
        "min_absolute": 80, "ratio_threshold": 3.5, "ratio_min": 55
    }
}

# --- CAMERA SELECTION ---
RAW_CAM_MODEL = os.getenv("CREDO_CAMERA_MODEL", "Logitech C920")
if "C270" in RAW_CAM_MODEL.upper():
    PROFILE_KEY = "Logitech C270"
    PROFILE = HARDWARE_PROFILES["C270_FAMILY"]
elif "C922" in RAW_CAM_MODEL.upper():
    PROFILE_KEY = "Logitech C922"
    PROFILE = HARDWARE_PROFILES["C922_FAMILY"]
else:
    PROFILE_KEY = "Generic Camera"
    PROFILE = HARDWARE_PROFILES["GENERIC"]

# --- API ENDPOINTS ---
CREDO_BASE_URL = "https://api.credo.science/api/v2"
LOGIN_URL = f"{CREDO_BASE_URL}/user/login"
PING_URL = f"{CREDO_BASE_URL}/ping"
DETECT_URL = f"{CREDO_BASE_URL}/detection"

# --- SETTINGS ---
CAMERA_INDEX = 0
CAPTURE_WIDTH = 1280; CAPTURE_HEIGHT = 720; CROP_SIZE = 64
PING_INTERVAL = 600
LAT = float(os.getenv("CREDO_LAT"))
LNG = float(os.getenv("CREDO_LNG"))
ALT = float(os.getenv("CREDO_ALT"))

# --- STATE ---
bg_frame = None; alpha = 0.01; current_token = None
SCRIPT_START = int(time.time() * 1000); LAST_PING = SCRIPT_START
last_frame_time = time.time()
data_cache = []          # Capped at 1000 entries; plain list for JSON serialisation
connected_clients = set()

# --- ATMOSPHERIC STATE ---
cached_pressure = 1013.25 
cached_outdoor_temp = None
last_pressure_check = 0
PRESSURE_CACHE_DURATION = 900 

def get_pressure():
    global cached_pressure, cached_outdoor_temp, last_pressure_check
    now = time.time()
    if (now - last_pressure_check) < PRESSURE_CACHE_DURATION:
        return cached_pressure
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LNG}&current=surface_pressure,temperature_2m"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            current = data.get('current', {})
            if 'surface_pressure' in current:
                cached_pressure = current['surface_pressure']
            if 'temperature_2m' in current:
                cached_outdoor_temp = current['temperature_2m']
            last_pressure_check = now
            logging.info(f"[WX] Updated: {cached_pressure} hPa | {cached_outdoor_temp}°C outdoor")
    except Exception as e:
        logging.error(f"[WX] Update Error: {e}")
    return cached_pressure

def get_outdoor_temp():
    return cached_outdoor_temp

# --- DIAGNOSTIC TRACE LOGGER ---
# Uses a RotatingFileHandler: max 2 MB per file, keeps 5 backups (~10 MB total).
# CSV format so the data can be loaded directly into pandas / Excel for analysis.
DIAG_LOG_FILE = os.path.join(BASE_DIR, "trace_diagnostics.csv")

_diag_handler = logging.handlers.RotatingFileHandler(
    DIAG_LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_diag_handler.setFormatter(logging.Formatter("%(message)s"))  # raw CSV rows

diag_logger = logging.getLogger("TraceDiagnostics")
diag_logger.setLevel(logging.INFO)
diag_logger.addHandler(_diag_handler)
diag_logger.propagate = False  # don't echo to the main console stream

# Write CSV header only when the file is new / empty
if not os.path.exists(DIAG_LOG_FILE) or os.path.getsize(DIAG_LOG_FILE) == 0:
    diag_logger.info("timestamp_iso,status,max_val,bg_val,ratio,loc_x,loc_y,note")


def log_trace_event(status, max_val, bg_val, coord, note=""):
    """
    Appends one CSV row to the rotating diagnostic log.
    status: 'ACCEPTED' | 'REJECTED_SENSITIVITY' | 'REJECTED_SPATIAL'
    """
    ratio = (max_val / bg_val) if bg_val > 0 else float("inf")
    row = (
        f"{datetime.utcnow().isoformat()},"
        f"{status},{int(max_val)},{int(bg_val)},{ratio:.2f},"
        f"{coord[0]},{coord[1]},{note}"
    )
    diag_logger.info(row)


def is_spatially_valid(diff_frame, x, y, peak_val):
    """
    Returns True if the 3x3 neighbourhood around (x, y) contains more than one
    pixel above 50 % of the peak value — i.e. the hit is NOT an isolated hot pixel.
    """
    roi = diff_frame[max(0, y - 1):y + 2, max(0, x - 1):x + 2]
    return int(np.sum(roi >= (peak_val * 0.5))) > 1

# --- WEBSOCKET LOGIC (MODULE-LEVEL) ---

async def handler(websocket):
    """Handles the lifecycle of a single dashboard client connection."""
    client_info = websocket.remote_address
    connected_clients.add(websocket)
    logging.info(f"[WS] Client connected: {client_info}")

    try:
        # Initial sync: send historical data and script start time
        await websocket.send(json.dumps({
            "type": "init",
            "data": list(data_cache),
            "script_start": SCRIPT_START
        }))
        # Keep connection alive; listen for any client messages
        async for message in websocket:
            pass

    except (ConnectionClosedError, ConnectionClosedOK):
        logging.info(f"[WS] Client {client_info} disconnected normally.")
    except Exception as e:
        logging.error(f"[WS] Error with client {client_info}: {e}")
    finally:
        connected_clients.discard(websocket)
        logging.info(f"[WS] Client {client_info} removed from active set.")


async def broadcast():
    """Background task: push telemetry updates to all active dashboard clients."""
    while True:
        await asyncio.sleep(1)
        try:
            now = time.time()
            sys_status = "SYSTEM_STALLED" if (now - last_frame_time > 5.0) else "OK"

            payload = {
                "type": "update",
                "timestamp": int(now * 1000),
                "temp": get_outdoor_temp() or 0.0,
                "pressure": get_pressure() or 0.0,
                "system_status": sys_status
            }

            # Update cache and cap memory usage
            data_cache.append(payload)
            if len(data_cache) > 1000:
                data_cache.pop(0)

            if not connected_clients:
                continue

            msg = json.dumps(payload)
            # Broadcast guard: prune dead connections on send failure
            for ws in list(connected_clients):
                try:
                    await ws.send(msg)
                except (ConnectionClosedError, ConnectionClosedOK, Exception):
                    connected_clients.discard(ws)

        except Exception as e:
            logging.error(f"[WS] Broadcast loop error: {e}")


def ws_thread_target():
    """Entry point for the dedicated WebSocket server thread."""
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)

    async def main_ws():
        logging.info("[WS] Server starting on port 8001...")
        broadcast_task = asyncio.create_task(broadcast())
        try:
            async with websockets.serve(
                handler, "0.0.0.0", 8001,
                ping_interval=20,
                ping_timeout=20
            ):
                await asyncio.Future()  # Run forever
        finally:
            broadcast_task.cancel()

    try:
        new_loop.run_until_complete(main_ws())
    finally:
        new_loop.close()

def tune_driver():
    logging.info(f"[HARDWARE] Tuning {PROFILE_KEY} ({PROFILE['driver_quirk']})...")
    v = f"/dev/video{CAMERA_INDEX}"
    try:
        subprocess.run(["v4l2-ctl", "-d", v, "--set-ctrl=auto_exposure=1"], stderr=subprocess.DEVNULL)
        subprocess.run(["v4l2-ctl", "-d", v, "--set-ctrl=exposure_auto=1"], stderr=subprocess.DEVNULL)
        if PROFILE['driver_quirk'] == "legacy":
            subprocess.run(["v4l2-ctl", "-d", v, f"--set-ctrl=exposure_time_absolute={PROFILE['exposure']}"], stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["v4l2-ctl", "-d", v, f"--set-ctrl=exposure_absolute={PROFILE['exposure']}"], stderr=subprocess.DEVNULL)
        subprocess.run(["v4l2-ctl", "-d", v, f"--set-ctrl=gain={PROFILE['gain']}"], stderr=subprocess.DEVNULL)
        subprocess.run(["v4l2-ctl", "-d", v, "--set-ctrl=white_balance_automatic=0"], stderr=subprocess.DEVNULL)
        logging.info(f"[HARDWARE] Tuned. Exp:{PROFILE['exposure']} Gain:{PROFILE['gain']}")
    except: logging.error("[HARDWARE] Tuning warning.")

def get_temp():
    try: return float(subprocess.check_output(["vcgencmd", "measure_temp"]).decode().replace("temp=","").replace("'C\n",""))
    except: return 0.0

def get_meta(pressure_val=None):
    meta_dict = {
        "device_model": DEVICE_MODEL,
        "camera_model": PROFILE_KEY,
        "station_id": STATION_ID,
        "operator": CREDO_USER,
        "temperature": get_outdoor_temp(),
        "software_version": SOFTWARE_VERSION
    }
    if pressure_val: meta_dict["pressure_hpa"] = pressure_val
    return json.dumps(meta_dict)

def update_status_file(is_connected):
    try:
        with open(LOCAL_STATUS_FILE, "w") as f:
            json.dump({
                "connected": is_connected, 
                "timestamp": time.time(), 
                "station": STATION_ID, 
                "operator": CREDO_USER,
                "pressure_hpa": get_pressure(),
                "camera_model": PROFILE_KEY,
                "lat": LAT,
                "lng": LNG
            }, f)
    except: pass

def payload_inspector(tag, data):
    try:
        debug_copy = copy.deepcopy(data)
        if 'detections' in debug_copy:
            for d in debug_copy['detections']:
                if 'frame_content' in d:
                    d['frame_content'] = "<BASE64_IMAGE_DATA_TRUNCATED>"
        logging.info(f"[{tag}] Payload Inspector:\n{json.dumps(debug_copy, indent=2)}")
    except Exception as e:
        logging.error(f"[{tag}] Inspector Failed: {e}")

def login():
    global current_token
    try:
        logging.info(f"[AUTH] Attempting Login for {CREDO_USER}...")
        r = requests.post(LOGIN_URL, json={
            "username": CREDO_USER, "password": CREDO_PASS, "device_id": STATION_ID,
            "device_type": "detector", "device_model": DEVICE_MODEL, 
            "system_version": "Linux", "app_version": SOFTWARE_VERSION
        }, timeout=20)
        
        if r.status_code == 200: 
            current_token = r.json().get('token')
            logging.info("[AUTH] Login Successful. Token acquired.")
            update_status_file(True)
            return True
        else:
            logging.error(f"[AUTH] Login Failed. Status: {r.status_code} | Body: {r.text}")
            update_status_file(False)
            return False
    except Exception as e: 
        logging.error(f"[AUTH] Connection Error during Login: {e}")
        update_status_file(False)
        return False

def ping():
    global current_token, LAST_PING
    while True:
        if not current_token: login(); time.sleep(5); continue
        try:
            now = int(time.time()*1000)
            payload = {
                "device_id": STATION_ID, "device_type": "detector", "device_model": DEVICE_MODEL,
                "system_version": "Linux", "app_version": SOFTWARE_VERSION, "timestamp": now,
                "delta_time": now-LAST_PING, "on_time": now-SCRIPT_START,
                "lat": LAT, "lng": LNG, "provider": "network", "metadata": get_meta(get_pressure())
            }
            payload_inspector("PING", payload)
            r = requests.post(PING_URL, json=payload, headers={"Authorization": f"Token {current_token}"}, timeout=15)
            if r.status_code == 200: 
                logging.info(f"[NET] Ping Sent. P:{cached_pressure}hPa")
                LAST_PING = now
            elif r.status_code == 401: 
                logging.warning(f"[NET] 401 Unauthorized. Re-login required.")
                current_token = None
                update_status_file(False)
            else:
                logging.error(f"[NET] Ping Failed ({r.status_code}): {r.text}")
        except Exception as e:
            logging.error(f"[NET] Error: {e}")
        
        # Keep local status file updated with latest pressure even if no detections
        update_status_file(True if current_token else False)    
        time.sleep(PING_INTERVAL)

def upload_worker(payload):
    global current_token
    try:
        if not current_token: login()
        payload_inspector("UPLOAD", payload)
        r = requests.post(DETECT_URL, json=payload, headers={"Authorization": f"Token {current_token}"}, timeout=20)
        if r.status_code == 200:
            logging.info(f"[UPLOAD] Cloud Success. Server Replied: {r.text}")
        else:
            logging.error(f"[UPLOAD] Cloud Failed ({r.status_code}). Server Replied: {r.text}")
    except Exception as e:
        logging.error(f"[UPLOAD] Cloud Error: {e}")

def save_local_log(payload):
    try:
        detection_data = payload["detections"][0]
        data_list = []
        if os.path.exists(LOCAL_LOG_FILE):
            try:
                with open(LOCAL_LOG_FILE, 'r') as f:
                    data_list = json.load(f)
                    if len(data_list) > 1000: data_list = data_list[-1000:] 
            except json.JSONDecodeError: data_list = []

        data_list.append(detection_data)
        with open(LOCAL_LOG_FILE, 'w') as f:
            json.dump(data_list, f, indent=4)
        logging.info(f"[LOCAL] Saved for Dashboard.")
    except Exception as e:
        logging.error(f"[LOCAL] Write Failed: {e}")

def process_hit(frame, x, y, max_val):
    h, w = frame.shape
    hc = CROP_SIZE // 2
    sx, sy = max(0, min(x-hc, w-CROP_SIZE)), max(0, min(y-hc, h-CROP_SIZE))
    
    raw_b64 = base64.b64encode(cv2.imencode('.png', frame[sy:sy+CROP_SIZE, sx:sx+CROP_SIZE])[1]).decode('utf-8')
    web_b64 = f"data:image/png;base64,{raw_b64}"
    current_pressure = get_pressure()
    current_outdoor_temp = get_outdoor_temp()
    
    cloud_payload = {
        "device_id": STATION_ID, "device_type": "detector", "device_model": DEVICE_MODEL,
        "system_version": "Linux", "app_version": SOFTWARE_VERSION,
        "detections": [{
            "timestamp": int(time.time()*1000), "frame_content": raw_b64, 
            "x": int(x), "y": int(y), "width": w, "height": h, "max": int(max_val),  
            "metadata": get_meta(current_pressure),
            "latitude": LAT, "longitude": LNG, "altitude": ALT, "accuracy": 50, "provider": "network"
        }]
    }

    local_payload = {
        "detections": [{
            "timestamp": int(time.time()*1000), 
            "frame_content": web_b64,
            "x": int(x), "y": int(y), "max": int(max_val),
            "pressure": current_pressure,
            "outdoor_temp": current_outdoor_temp,
            "camera_model": PROFILE_KEY,
            "metadata": get_meta(current_pressure)
        }]
    }
    
    threading.Thread(target=upload_worker, args=(cloud_payload,)).start()
    threading.Thread(target=save_local_log, args=(local_payload,)).start()
    logging.info(f"[HIT] Energy:{max_val} | P:{current_pressure}hPa | Outdoor:{current_outdoor_temp}C")

def config_watcher():
    global CREDO_USER, CREDO_PASS, STATION_ID, DEVICE_MODEL, SOFTWARE_VERSION, LAT, LNG, ALT, current_token
    last_mtime = 0
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        env_path = ".env"
        
    while True:
        try:
            if os.path.exists(env_path):
                current_mtime = os.path.getmtime(env_path)
                if current_mtime > last_mtime:
                    if last_mtime != 0:
                        load_dotenv(dotenv_path=env_path, override=True)
                        old_user = CREDO_USER
                        old_pass = CREDO_PASS
                        
                        CREDO_USER = os.getenv("CREDO_USER")
                        CREDO_PASS = os.getenv("CREDO_PASS")
                        STATION_ID = os.getenv("CREDO_STATION")
                        DEVICE_MODEL = os.getenv("CREDO_DEVICE_MODEL")
                        SOFTWARE_VERSION = os.getenv("CREDO_SOFTWARE_VERSION")
                        
                        try: LAT = float(os.getenv("CREDO_LAT", LAT))
                        except: pass
                        try: LNG = float(os.getenv("CREDO_LNG", LNG))
                        except: pass
                        try: ALT = float(os.getenv("CREDO_ALT", ALT))
                        except: pass
                        
                        if old_user != CREDO_USER or old_pass != CREDO_PASS:
                            current_token = None
                            logging.info(f"[CONFIG] Live Operator update detected ({CREDO_USER}).")
                        
                        update_status_file(True if current_token else False)
                    last_mtime = current_mtime
        except Exception:
            pass
        time.sleep(2)

if __name__ == "__main__":
    logging.info(f"--- CMOSmicRay {SOFTWARE_VERSION} ---")
    logging.info(f"[CONFIG] Station: {STATION_ID} | Operator: {CREDO_USER}")
    logging.info(f"[CONFIG] Location: {LAT}, {LNG} (Alt: {ALT}m)")
    
    get_pressure() 
    tune_driver()
    threading.Thread(target=ping, daemon=True).start()
    threading.Thread(target=ws_thread_target, daemon=True).start()
    threading.Thread(target=config_watcher, daemon=True).start()

    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
    cap.set(3, CAPTURE_WIDTH); cap.set(4, CAPTURE_HEIGHT); cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    time.sleep(2)
    
    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(1)
            continue

        # Watchdog: record frame arrival for SYSTEM_STALLED detection
        last_frame_time = time.time()

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if bg_frame is None:
            bg_frame = gray.astype("float")
            continue

        cv2.accumulateWeighted(gray, bg_frame, alpha)
        diff = cv2.absdiff(gray, cv2.convertScaleAbs(bg_frame))
        _, maxVal, _, maxLoc = cv2.minMaxLoc(diff)

        x, y = maxLoc
        bg_brightness = int(bg_frame[y, x])

        # --- Gate 1: Sensitivity checks ---
        # Absolute gate: raw diff must exceed the hard minimum for this sensor
        cond_absolute = maxVal >= PROFILE['min_absolute']
        # Adaptive gate: diff must be a significant multiple of local background
        #   AND must clear the ratio floor (avoids false positives on dark frames)
        cond_adaptive = (
            maxVal >= PROFILE['ratio_threshold'] * bg_brightness
            and maxVal >= PROFILE['ratio_min']
        )
        # Edge guard: ignore hits right on the frame border (driver artefacts)
        cond_not_edge = (x > 0 and y > 0)

        if (cond_absolute or cond_adaptive) and cond_not_edge:
            # --- Gate 2: Spatial hardening — reject isolated hot pixels ---
            if is_spatially_valid(diff, x, y, maxVal):
                log_trace_event("ACCEPTED", maxVal, bg_brightness, maxLoc)
                process_hit(diff, x, y, maxVal)
                time.sleep(0.5)          # brief cooldown after a confirmed hit
            else:
                log_trace_event(
                    "REJECTED_SPATIAL", maxVal, bg_brightness, maxLoc,
                    "Isolated pixel — failed 3x3 neighbourhood check"
                )
        elif maxVal > PROFILE['min_absolute'] * 0.7:
            # Near-miss: reached 70 % of the absolute threshold — log for tuning
            log_trace_event(
                "REJECTED_SENSITIVITY", maxVal, bg_brightness, maxLoc,
                "Near-miss (<threshold)"
            )

        time.sleep(0.02)
