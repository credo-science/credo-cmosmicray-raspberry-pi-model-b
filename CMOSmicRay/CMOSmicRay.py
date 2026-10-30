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
import queue
import signal
import sys
import logging
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from dotenv import load_dotenv

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")

# --- HARDWARE PROFILES ---
HARDWARE_PROFILES = {
    "C270_FAMILY": {
        "threshold": 60, "exposure": 1000, "gain": 255, "driver_quirk": "legacy"
    },
    "C922_FAMILY": {
        "threshold": 95, "exposure": 150, "gain": 128, "driver_quirk": "standard"
    },
    "GENERIC": {
        "threshold": 80, "exposure": 500, "gain": 150, "driver_quirk": "standard"
    }
}


def _select_camera_profile(raw_model):
    upper = raw_model.upper()
    if "C270" in upper:
        return "Logitech C270", HARDWARE_PROFILES["C270_FAMILY"]
    if "C922" in upper:
        return "Logitech C922", HARDWARE_PROFILES["C922_FAMILY"]
    return "Generic Camera", HARDWARE_PROFILES["GENERIC"]


# --- CONFIGURATION ---
_REQUIRED_ENV = [
    "CREDO_STATION", "CREDO_USER", "CREDO_PASS", "CREDO_DEVICE_MODEL",
    "CREDO_SOFTWARE_VERSION", "CREDO_LAT", "CREDO_LNG", "CREDO_ALT",
]


@dataclass(frozen=True)
class AppConfig:
    """Validated runtime configuration, loaded once from the environment.

    Grouping these into one immutable object (instead of ~10 loose module
    globals) means every function that needs config takes/reads `config`
    explicitly, and tests can construct a fresh instance per test instead of
    monkeypatching individual module-level names.
    """
    station_id: str
    credo_user: str
    credo_pass: str
    device_model: str
    software_version: str
    lat: float
    lng: float
    alt: float
    camera_profile_key: str
    camera_profile: dict
    public_dir: str

    @classmethod
    def from_env(cls, base_dir):
        """Loads and validates configuration from the environment. Exits the
        process with an actionable message on missing/invalid values.

        This is the production entrypoint's fail-fast startup check -- it's
        only ever called under `__main__` (not at module scope), so
        `import CMOSmicRay` from a test file never triggers it even when
        .env isn't present.
        """
        missing = [name for name in _REQUIRED_ENV if not os.getenv(name)]
        if missing:
            logging.error(
                f"[CONFIG] Missing required environment variable(s): {', '.join(missing)}. "
                f"Copy .env.example to .env and fill in your CREDO credentials/location, "
                f"or export them before launching."
            )
            sys.exit(1)
        try:
            lat = float(os.getenv("CREDO_LAT"))
            lng = float(os.getenv("CREDO_LNG"))
            alt = float(os.getenv("CREDO_ALT"))
        except ValueError:
            logging.error("[CONFIG] CREDO_LAT/CREDO_LNG/CREDO_ALT must be numeric.")
            sys.exit(1)

        profile_key, profile = _select_camera_profile(os.getenv("CREDO_CAMERA_MODEL", "Logitech C920"))

        return cls(
            station_id=os.getenv("CREDO_STATION"),
            credo_user=os.getenv("CREDO_USER"),
            credo_pass=os.getenv("CREDO_PASS"),
            device_model=os.getenv("CREDO_DEVICE_MODEL"),
            software_version=os.getenv("CREDO_SOFTWARE_VERSION"),
            lat=lat, lng=lng, alt=alt,
            camera_profile_key=profile_key,
            camera_profile=profile,
            public_dir=os.path.join(base_dir, "public"),
        )


config = None  # set by AppConfig.from_env() under __main__

# --- API ENDPOINTS ---
CREDO_BASE_URL = "https://api.credo.science/api/v2"
LOGIN_URL = f"{CREDO_BASE_URL}/user/login"
PING_URL = f"{CREDO_BASE_URL}/ping"
DETECT_URL = f"{CREDO_BASE_URL}/detection"

# --- SETTINGS ---
CAMERA_INDEX = 0
CAPTURE_WIDTH = 1280; CAPTURE_HEIGHT = 720; CROP_SIZE = 64
PING_INTERVAL = 600
LOGIN_RETRY_BASE = 5
LOGIN_RETRY_MAX = 300  # cap re-login backoff at 5 minutes
LOOP_PROFILE_INTERVAL = 60  # seconds between capture-loop profiling summaries

# --- STATE ---
SCRIPT_START = int(time.time() * 1000); LAST_PING = SCRIPT_START


class AuthState:
    """Holds the CREDO session token and the lock protecting it against
    concurrent re-login storms (see tests/test_auth.py's concurrent-login
    regression test). Uses an RLock so login() can hold the lock across its
    whole check-then-network-then-set sequence while still calling
    get_token()/set_token() internally.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self._token = None

    def get_token(self):
        with self.lock:
            return self._token

    def set_token(self, token):
        with self.lock:
            self._token = token

    def clear_token(self):
        self.set_token(None)


class EnvironmentState:
    """Caches the last-fetched barometric pressure / outdoor temperature so
    every detection doesn't trigger a fresh Open-Meteo API call."""

    CACHE_DURATION_S = 900

    def __init__(self):
        self.pressure_hpa = 1013.25
        self.outdoor_temp_c = None
        self._last_check = 0

    def get_pressure(self, lat, lng):
        now = time.time()
        if (now - self._last_check) < self.CACHE_DURATION_S:
            return self.pressure_hpa
        try:
            url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&current=surface_pressure,temperature_2m"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                current = data.get('current', {})
                if 'surface_pressure' in current:
                    self.pressure_hpa = current['surface_pressure']
                if 'temperature_2m' in current:
                    self.outdoor_temp_c = current['temperature_2m']
                self._last_check = now
                logging.info(f"[WX] Updated: {self.pressure_hpa} hPa | {self.outdoor_temp_c}°C outdoor")
        except requests.RequestException as e:
            logging.error(f"[WX] Update Error: {e}")
        return self.pressure_hpa

    def get_outdoor_temp(self):
        return self.outdoor_temp_c


class LocalStore:
    """Owns the local JSON files the dashboard reads (detections.json,
    status.json) and the single-writer queue that serializes writes to
    detections.json -- concurrent writers would race on its
    read-modify-write cycle (see tests/test_local_log.py).
    """

    MAX_DETECTIONS = 1000

    def __init__(self, public_dir):
        self.log_file = os.path.join(public_dir, "detections.json")
        self.status_file = os.path.join(public_dir, "status.json")
        self.queue = queue.Queue()

    def save_local_log(self, payload):
        try:
            detection_data = payload["detections"][0]
            data_list = []
            if os.path.exists(self.log_file):
                try:
                    with open(self.log_file, 'r') as f:
                        data_list = json.load(f)
                except json.JSONDecodeError:
                    data_list = []

            data_list.append(detection_data)
            if len(data_list) > self.MAX_DETECTIONS:
                data_list = data_list[-self.MAX_DETECTIONS:]
            with open(self.log_file, 'w') as f:
                json.dump(data_list, f, indent=4)
            logging.info(f"[LOCAL] Saved for Dashboard.")
        except (OSError, KeyError, IndexError) as e:
            logging.error(f"[LOCAL] Write Failed: {e}")

    def writer_loop(self):
        while True:
            payload = self.queue.get()
            try:
                self.save_local_log(payload)
            except Exception as e:
                # task_done() must always fire, even on an unexpected error --
                # otherwise shutdown()'s queue.join() blocks forever on this
                # one un-acked item and the process can't exit cleanly.
                logging.error(f"[LOCAL] Writer thread error: {e}")
            finally:
                self.queue.task_done()

    def update_status(self, is_connected, config, environment):
        try:
            with open(self.status_file, "w") as f:
                json.dump({
                    "connected": is_connected,
                    "timestamp": time.time(),
                    "station": config.station_id,
                    "operator": config.credo_user,
                    "pressure_hpa": environment.get_pressure(config.lat, config.lng),
                    "camera_model": config.camera_profile_key,
                    # Rounded to ~1km precision -- this file is served
                    # unauthenticated by the dashboard's http.server, so
                    # full-precision coordinates (which CREDO still receives
                    # at full precision, see process_hit) don't belong in a
                    # public, unauthenticated endpoint.
                    "lat": round(config.lat, 2) if config.lat is not None else None,
                    "lng": round(config.lng, 2) if config.lng is not None else None
                }, f)
        except OSError as e:
            logging.error(f"[STATUS] Write Failed: {e}")


class MotionDetector:
    """Frame-differencing background-subtraction detector. Owns the running
    background estimate (bg_frame) instead of it being a bare module global.
    """

    def __init__(self, alpha=0.01):
        self.alpha = alpha
        self.bg_frame = None

    def detect(self, gray_frame):
        """Returns (max_val, x, y, diff) for the strongest deviation from the
        rolling background, or None on the frame that just seeded it."""
        if self.bg_frame is None:
            self.bg_frame = gray_frame.astype("float")
            return None
        cv2.accumulateWeighted(gray_frame, self.bg_frame, self.alpha)
        diff = cv2.absdiff(gray_frame, cv2.convertScaleAbs(self.bg_frame))
        _, max_val, _, max_loc = cv2.minMaxLoc(diff)
        return max_val, max_loc[0], max_loc[1], diff


auth = AuthState()
environment = EnvironmentState()
store = LocalStore(PUBLIC_DIR)

# Bounded upload concurrency so a burst of hits can't spawn unbounded threads.
upload_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="upload")


def tune_driver(profile_key, profile):
    logging.info(f"[HARDWARE] Tuning {profile_key} ({profile['driver_quirk']})...")
    v = f"/dev/video{CAMERA_INDEX}"
    try:
        subprocess.run(["v4l2-ctl", "-d", v, "--set-ctrl=auto_exposure=1"], stderr=subprocess.DEVNULL)
        subprocess.run(["v4l2-ctl", "-d", v, "--set-ctrl=exposure_auto=1"], stderr=subprocess.DEVNULL)
        if profile['driver_quirk'] == "legacy":
            subprocess.run(["v4l2-ctl", "-d", v, f"--set-ctrl=exposure_time_absolute={profile['exposure']}"], stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["v4l2-ctl", "-d", v, f"--set-ctrl=exposure_absolute={profile['exposure']}"], stderr=subprocess.DEVNULL)
        subprocess.run(["v4l2-ctl", "-d", v, f"--set-ctrl=gain={profile['gain']}"], stderr=subprocess.DEVNULL)
        subprocess.run(["v4l2-ctl", "-d", v, "--set-ctrl=white_balance_automatic=0"], stderr=subprocess.DEVNULL)
        logging.info(f"[HARDWARE] Tuned. Exp:{profile['exposure']} Gain:{profile['gain']}")
    except (subprocess.SubprocessError, OSError) as e:
        logging.error(f"[HARDWARE] Tuning warning: {e}")


def get_temp():
    try:
        raw = subprocess.check_output(["vcgencmd", "measure_temp"]).decode()
        return float(raw.replace("temp=", "").replace("'C\n", ""))
    except (subprocess.SubprocessError, OSError, ValueError):
        return 0.0


def get_meta(pressure_val=None):
    meta_dict = {
        "device_model": config.device_model,
        "camera_model": config.camera_profile_key,
        "station_id": config.station_id,
        "operator": config.credo_user,
        "temperature": environment.get_outdoor_temp(),
        "software_version": config.software_version
    }
    if pressure_val: meta_dict["pressure_hpa"] = pressure_val
    return json.dumps(meta_dict)


def payload_inspector(tag, data):
    try:
        debug_copy = copy.deepcopy(data)
        if 'detections' in debug_copy:
            for d in debug_copy['detections']:
                if 'frame_content' in d:
                    d['frame_content'] = "<BASE64_IMAGE_DATA_TRUNCATED>"
        logging.info(f"[{tag}] Payload Inspector:\n{json.dumps(debug_copy, indent=2)}")
    except (TypeError, ValueError) as e:
        logging.error(f"[{tag}] Inspector Failed: {e}")


def login():
    with auth.lock:
        if auth.get_token():
            return True
        try:
            r = requests.post(LOGIN_URL, json={
                "username": config.credo_user, "password": config.credo_pass, "device_id": config.station_id,
                "device_type": "detector", "device_model": config.device_model,
                "system_version": "Linux", "app_version": config.software_version
            }, timeout=20)
            if r.status_code == 200:
                auth.set_token(r.json().get('token'))
                logging.info("[AUTH] Login Successful.")
                store.update_status(True, config, environment)
                return True
            store.update_status(False, config, environment)
            return False
        except requests.RequestException as e:
            logging.error(f"[AUTH] Login Error: {e}")
            store.update_status(False, config, environment)
            return False


def ping():
    global LAST_PING
    retry_delay = LOGIN_RETRY_BASE
    while True:
        if not auth.get_token():
            login()
            if not auth.get_token():
                # Exponential backoff so a prolonged auth failure (bad
                # credentials, CREDO outage) doesn't hammer the login
                # endpoint every 5s indefinitely.
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, LOGIN_RETRY_MAX)
                continue
            retry_delay = LOGIN_RETRY_BASE
        try:
            now = int(time.time()*1000)
            # CREDO sums on_time server-side, so it must be the time since the
            # last successful ping (not cumulative since start). Coordinates
            # belong only in detections; the ping schema has no lat/lng.
            delta = now-LAST_PING
            payload = {
                "device_id": config.station_id, "device_type": "detector", "device_model": config.device_model,
                "system_version": "Linux", "app_version": config.software_version, "timestamp": now,
                "delta_time": delta, "on_time": delta, "provider": "network",
                "metadata": get_meta(environment.get_pressure(config.lat, config.lng))
            }
            payload_inspector("PING", payload)
            r = requests.post(PING_URL, json=payload, headers={"Authorization": f"Token {auth.get_token()}"}, timeout=15)
            if r.status_code == 200:
                logging.info(f"[NET] Ping Sent. P:{environment.pressure_hpa}hPa")
                LAST_PING = now
            elif r.status_code == 401:
                logging.warning(f"[NET] 401 Unauthorized. Re-login required.")
                auth.clear_token()
                store.update_status(False, config, environment)
            else:
                logging.error(f"[NET] Ping Failed ({r.status_code}): {r.text}")
        except requests.RequestException as e:
            logging.error(f"[NET] Error: {e}")
        except Exception as e:
            # This loop is the resilience boundary for the daemon thread --
            # unlike leaf functions, it must never let an unexpected
            # exception (e.g. a JSON decode error on an older requests
            # version) kill the ping/re-login heartbeat permanently.
            logging.error(f"[NET] Unexpected error, continuing: {e}")

        # Keep local status file updated with latest pressure even if no detections
        store.update_status(True if auth.get_token() else False, config, environment)
        time.sleep(PING_INTERVAL)


def upload_worker(payload):
    try:
        if not auth.get_token(): login()
        payload_inspector("UPLOAD", payload)
        r = requests.post(DETECT_URL, json=payload, headers={"Authorization": f"Token {auth.get_token()}"}, timeout=20)
        if r.status_code == 200:
            logging.info(f"[UPLOAD] Cloud Success. Server Replied: {r.text}")
        elif r.status_code == 401:
            logging.warning("[UPLOAD] 401 Unauthorized, forcing re-login.")
            auth.clear_token()
        else:
            logging.error(f"[UPLOAD] Cloud Failed ({r.status_code}). Server Replied: {r.text}")
    except requests.RequestException as e:
        logging.error(f"[UPLOAD] Cloud Error: {e}")


def process_hit(frame, x, y, max_val):
    h, w = frame.shape
    hc = CROP_SIZE // 2
    sx, sy = max(0, min(x-hc, w-CROP_SIZE)), max(0, min(y-hc, h-CROP_SIZE))

    raw_b64 = base64.b64encode(cv2.imencode('.png', frame[sy:sy+CROP_SIZE, sx:sx+CROP_SIZE])[1]).decode('utf-8')
    web_b64 = f"data:image/png;base64,{raw_b64}"
    current_pressure = environment.get_pressure(config.lat, config.lng)
    current_outdoor_temp = environment.get_outdoor_temp()

    cloud_payload = {
        "device_id": config.station_id, "device_type": "detector", "device_model": config.device_model,
        "system_version": "Linux", "app_version": config.software_version,
        "detections": [{
            "timestamp": int(time.time()*1000), "frame_content": raw_b64,
            "x": int(x), "y": int(y), "width": w, "height": h, "max": int(max_val),
            "metadata": get_meta(current_pressure),
            "latitude": config.lat, "longitude": config.lng, "altitude": config.alt, "accuracy": 50, "provider": "network"
        }]
    }

    local_payload = {
        "detections": [{
            "timestamp": int(time.time()*1000),
            "frame_content": web_b64,
            "x": int(x), "y": int(y), "max": int(max_val),
            "pressure": current_pressure,
            "outdoor_temp": current_outdoor_temp,
            "camera_model": config.camera_profile_key,
            "metadata": get_meta(current_pressure)
        }]
    }

    upload_executor.submit(upload_worker, cloud_payload)
    store.queue.put(local_payload)
    logging.info(f"[HIT] Energy:{max_val} | P:{current_pressure}hPa | Outdoor:{current_outdoor_temp}C")


def shutdown(cap):
    logging.info("[SHUTDOWN] Draining pending uploads and log writes...")
    cap.release()
    upload_executor.shutdown(wait=True, cancel_futures=False)
    store.queue.join()
    sys.exit(0)


# --- CAPTURE LOOP PROFILING ---
# Lightweight instrumentation to measure real capture-loop timing on target
# hardware before deciding whether/how to throttle it (see TODOS.md). Logged
# periodically, not per-frame, so the logging call itself doesn't skew the
# numbers it's measuring.
_loop_profile = {
    "count": 0,
    "compute_s": 0.0,
    "compute_max_s": 0.0,
    "total_s": 0.0,
    "window_start": time.time(),
}


def _record_loop_time(compute_elapsed, total_elapsed):
    p = _loop_profile
    p["count"] += 1
    p["compute_s"] += compute_elapsed
    p["compute_max_s"] = max(p["compute_max_s"], compute_elapsed)
    p["total_s"] += total_elapsed

    now = time.time()
    window = now - p["window_start"]
    if window >= LOOP_PROFILE_INTERVAL and p["count"] > 0:
        avg_compute_ms = (p["compute_s"] / p["count"]) * 1000
        max_compute_ms = p["compute_max_s"] * 1000
        avg_total_ms = (p["total_s"] / p["count"]) * 1000
        iter_rate = p["count"] / window
        logging.info(
            f"[PROFILE] Capture loop: {iter_rate:.1f} iter/s | "
            f"cv2 compute avg {avg_compute_ms:.1f}ms max {max_compute_ms:.1f}ms | "
            f"total avg {avg_total_ms:.1f}ms (incl. cap.read() + sleeps) | "
            f"n={p['count']} over {window:.0f}s"
        )
        p["count"] = 0
        p["compute_s"] = 0.0
        p["compute_max_s"] = 0.0
        p["total_s"] = 0.0
        p["window_start"] = now


if __name__ == "__main__":
    config = AppConfig.from_env(BASE_DIR)
    os.makedirs(config.public_dir, exist_ok=True)
    logging.info(f"--- CMOSmicRay {config.software_version} ---")
    environment.get_pressure(config.lat, config.lng)
    tune_driver(config.camera_profile_key, config.camera_profile)
    threading.Thread(target=ping, daemon=True).start()
    threading.Thread(target=store.writer_loop, daemon=True).start()

    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
    cap.set(3, CAPTURE_WIDTH); cap.set(4, CAPTURE_HEIGHT); cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    signal.signal(signal.SIGINT, lambda sig, frame: shutdown(cap))
    signal.signal(signal.SIGTERM, lambda sig, frame: shutdown(cap))
    time.sleep(2)

    detector = MotionDetector()
    while True:
        iter_start = time.time()
        ret, frame = cap.read()
        if not ret: time.sleep(1); continue
        compute_start = time.time()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        result = detector.detect(gray)
        if result is None:
            continue
        max_val, x, y, diff = result
        compute_elapsed = time.time() - compute_start

        if max_val > config.camera_profile['threshold']:
            process_hit(diff, x, y, max_val)
            time.sleep(0.5)
        time.sleep(0.02)
        _record_loop_time(compute_elapsed, time.time() - iter_start)
