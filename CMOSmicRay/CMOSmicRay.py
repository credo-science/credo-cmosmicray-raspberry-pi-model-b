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
import re
import signal
import sys
import logging
import logging.handlers
import copy
import collections
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
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
# min_absolute   : peak difference at/above this is accepted outright
# ratio_threshold: adaptive gate -- peak must be >= ratio_threshold * local background
# ratio_min      : floor for the adaptive gate (ignores very dark backgrounds)
HARDWARE_PROFILES = {
    "C270_FAMILY": {
        "threshold": 60, "exposure": 660, "gain": 255, "driver_quirk": "legacy",   # C270 clamps exposure above 666 (100 us units)
        "min_absolute": 70, "ratio_threshold": 3.5, "ratio_min": 45
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


def utc_iso(ts=None):
    """Naive UTC ISO-8601 (the format the CSVs have always used for UTC)."""
    ts = time.time() if ts is None else ts
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None).isoformat()


def local_iso(ts=None, tz=None):
    """Station-local ISO-8601 *with* UTC offset, e.g. 2026-11-15T19:26:21.123-05:00.
    The offset keeps it unambiguous across the daylight-saving change. `tz`
    defaults to the system timezone (the Pi's configured zone)."""
    ts = time.time() if ts is None else ts
    return datetime.fromtimestamp(ts, tz).astimezone(tz).isoformat(timespec="milliseconds")


def detect_timezone_name():
    """IANA zone name of this machine: $STATION_TZ, else /etc/timezone, else the
    /etc/localtime symlink target. Returns "" if it cannot be determined."""
    name = os.getenv("STATION_TZ", "").strip()
    if name:
        return name
    try:
        with open("/etc/timezone", encoding="utf-8") as f:
            name = f.read().strip()
        if name:
            return name
    except OSError:
        pass
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return ""


def _retire_legacy_csv(path, header):
    """If `path` already exists with a different header (an older schema), move
    it aside so one file never mixes schemas. Rotated backups are left alone."""
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, encoding="utf-8", errors="replace") as f:
                first = f.readline().rstrip("\r\n")
            if first != header:
                dest = f"{os.path.splitext(path)[0]}.legacy-{int(time.time())}.csv"
                os.replace(path, dest)
                logging.info(f"[CSV] {os.path.basename(path)} had an older column layout; "
                             f"kept as {os.path.basename(dest)} and started a new file.")
    except OSError as e:
        logging.error(f"[CSV] Could not retire legacy file {path}: {e}")


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
    # Safety gates (all overridable from .env). Defaults match the values the
    # station ran with before the refactor (commit de70d2e).
    calibration_frames: int = 500
    obscuration_avg_max: float = 10.0
    obscuration_black_min: float = 0.05
    thermal_soft_c: float = 60.0
    thermal_ceiling_c: float = 70.0
    thermal_check_interval: int = 30
    stats_window_s: int = 3600   # live-time log window
    station_tz: str = ""          # IANA name (e.g. America/New_York); "" = unknown
    # Capture size. Default 640x480 (the default since commit de70d2e, 20 May 2026): pre-v1.7
    # data showed 65% hot-spot hits at 1280x720 vs 1% at 640x480. The profile thresholds date
    # from v1.3.0 (14 Apr 2026), which hard-coded 1280x720, so they were NOT tuned at 640x480.
    capture_width: int = 640
    capture_height: int = 480
    # Persistent hot-pixel mask: a position that yields this many accepted hits
    # is treated as a sensor defect (0 disables the mask).
    hot_pixel_min_hits: int = 5
    hot_pixel_radius: int = 3

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

        def _num(name, default, cast=float):
            try:
                return cast(float(os.getenv(name, default)))
            except ValueError:
                logging.error(f"[CONFIG] {name} must be numeric.")
                sys.exit(1)

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
            calibration_frames=_num("CALIBRATION_FRAMES", 500, int),
            obscuration_avg_max=_num("OBSCURATION_AVG_MAX", 10.0),
            obscuration_black_min=_num("OBSCURATION_BLACK_MIN", 0.05),
            thermal_soft_c=_num("THERMAL_SOFT_C", 60.0),
            thermal_ceiling_c=_num("THERMAL_CEILING_C", 70.0),
            thermal_check_interval=_num("THERMAL_CHECK_INTERVAL", 30, int),
            stats_window_s=_num("STATS_WINDOW_S", 3600, int),
            station_tz=detect_timezone_name(),
            capture_width=_num("CAPTURE_WIDTH", 640, int),
            capture_height=_num("CAPTURE_HEIGHT", 480, int),
            hot_pixel_min_hits=_num("HOT_PIXEL_MIN_HITS", 5, int),
            hot_pixel_radius=_num("HOT_PIXEL_RADIUS", 3, int),
        )


config = None  # set by AppConfig.from_env() under __main__

# --- API ENDPOINTS ---
CREDO_BASE_URL = "https://api.credo.science/api/v2"
LOGIN_URL = f"{CREDO_BASE_URL}/user/login"
PING_URL = f"{CREDO_BASE_URL}/ping"
DETECT_URL = f"{CREDO_BASE_URL}/detection"

# --- SETTINGS ---
CAMERA_INDEX = 0
CROP_SIZE = 64
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
        # Append-only archive lives beside public/ (not inside it) so the
        # dashboard's http.server never serves it.
        self.archive_file = os.path.join(os.path.dirname(os.path.abspath(public_dir)), "detections_archive.jsonl")
        self.queue = queue.Queue()
        # Safety-gate state merged into status.json for the dashboard
        # (calibration progress, CPU temp, last critical alert).
        self.status_extra = {}

    def append_archive(self, detection_data):
        """Crash-safe permanent record: one metadata-only JSON line per hit
        (no image), flushed and fsynced so a power cut loses at most the line
        being written. Never truncated, unlike the rolling detections.json."""
        record = {k: v for k, v in detection_data.items() if k != "frame_content"}
        try:
            with open(self.archive_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            logging.error(f"[ARCHIVE] Write Failed: {e}")

    def save_local_log(self, payload):
        try:
            detection_data = payload["detections"][0]
            self.append_archive(detection_data)
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
            # Atomic replace: the dashboard (or a power cut) never sees a
            # half-written detections.json.
            tmp_path = self.log_file + ".tmp"
            with open(tmp_path, 'w') as f:
                json.dump(data_list, f, indent=4)
            os.replace(tmp_path, self.log_file)
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
        # Build the document first, then replace the file atomically. Uses the
        # cached pressure on purpose: this is called from the capture loop, and
        # a weather refresh here could block it for up to the request timeout.
        doc = {
            **self.status_extra,
            "connected": is_connected,
            "timestamp": time.time(),
            "station": config.station_id,
            "operator": config.credo_user,
            "pressure_hpa": environment.pressure_hpa,
            "camera_model": config.camera_profile_key,
            "timezone": config.station_tz or None,
            # Rounded to ~1km precision -- this file is served
            # unauthenticated by the dashboard's http.server, so
            # full-precision coordinates (which CREDO still receives
            # at full precision, see process_hit) don't belong in a
            # public, unauthenticated endpoint.
            "lat": round(config.lat, 2) if config.lat is not None else None,
            "lng": round(config.lng, 2) if config.lng is not None else None
        }
        try:
            tmp_path = self.status_file + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(doc, f)
            os.replace(tmp_path, self.status_file)
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


# --- DETECTION GATES -------------------------------------------------------
ACCEPTED = "ACCEPTED"
REJECTED_SENSITIVITY = "REJECTED_SENSITIVITY"
REJECTED_SPATIAL = "REJECTED_SPATIAL"
REJECTED_HOT = "REJECTED_HOT"

MAX_TRACES_PER_FRAME = 3
NEAR_MISS_FRACTION = 0.7      # log as REJECTED_SENSITIVITY above 70% of min_absolute
OBSCURATION_BLACK_LEVEL = 5   # a pixel counts as "black" at gray <= this
OBSCURATION_SWEEP_S = 600     # periodic mid-run lens-cover check
CALIBRATION_LOG_EVERY = 100


def is_spatially_valid(diff_frame, x, y, peak_val):
    """True if the 3x3 neighbourhood around (x, y) holds more than one pixel at
    or above 50% of the peak, i.e. the hit is NOT a single isolated hot pixel."""
    roi = diff_frame[max(0, y - 1):y + 2, max(0, x - 1):x + 2]
    return int(np.sum(roi >= (peak_val * 0.5))) > 1


class HotPixelMask:
    """Learns fixed-position sensor defects from the detector's own output.

    Every ACCEPTED hit is recorded by (x, y). Once a position has `min_hits`
    hits it is "hot", and any later candidate within `radius` px of it is
    rejected as REJECTED_HOT. Counts persist to a JSON file, keyed by capture
    size, because hot-pixel coordinates are only meaningful at the resolution
    they were seen (the pre-v1.7 data showed 65% hot-spot hits at 1280x720 but
    1% at 640x480). min_hits <= 0 disables the mask.
    """

    def __init__(self, path, size_key, min_hits=5, radius=3):
        self.path = path
        self.size_key = size_key
        self.min_hits = min_hits
        self.radius = radius
        self.counts = {}   # "x,y" -> hits at this size_key
        self._hot = []     # [(x, y)] positions at/above min_hits
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                self.counts = dict(json.load(f).get(self.size_key, {}))
        except (OSError, ValueError, AttributeError):
            self.counts = {}
        self._rebuild()

    def _rebuild(self):
        self._hot = []
        for key, n in self.counts.items():
            if n >= self.min_hits > 0:
                x, y = key.split(",")
                self._hot.append((int(x), int(y)))

    def is_hot(self, x, y):
        r = self.radius
        return any(abs(x - hx) <= r and abs(y - hy) <= r for hx, hy in self._hot)

    def record(self, x, y):
        """Counts one accepted hit; returns True if (x, y) just became hot."""
        if self.min_hits <= 0:
            return False
        key = f"{int(x)},{int(y)}"
        self.counts[key] = self.counts.get(key, 0) + 1
        became_hot = self.counts[key] == self.min_hits
        if became_hot:
            self._rebuild()
            logging.warning(f"[HOTPIXEL] ({x},{y}) reached {self.min_hits} hits at {self.size_key}; masking it.")
        self._save()
        return became_hot

    def _save(self):
        """Merges this size's counts into the file (other sizes kept), atomically."""
        try:
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = {}
            data[self.size_key] = self.counts
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.path)
        except OSError as e:
            logging.error(f"[HOTPIXEL] Save failed: {e}")


def scan_frame(diff, bg_frame, profile, crop_size=CROP_SIZE, max_traces=MAX_TRACES_PER_FRAME, hot_mask=None):
    """Finds up to `max_traces` candidates in one difference image.

    After each accepted trace its crop-sized ROI is blacked out (on a private
    copy -- `diff` itself is left intact so the caller can still crop the
    original pixels) and the frame is re-scanned for a secondary trace.

    Returns a list of (status, max_val, x, y, bg_val) tuples, where status is
    ACCEPTED, REJECTED_SPATIAL, REJECTED_HOT (a learned sensor defect, only when
    `hot_mask` is given) or REJECTED_SENSITIVITY (a near-miss).
    """
    events = []
    work = diff
    copied = False
    hc = crop_size // 2
    for _ in range(max_traces):
        _, max_val, _, max_loc = cv2.minMaxLoc(work)
        x, y = max_loc
        bg_val = int(bg_frame[y, x])

        passes_absolute = max_val >= profile["min_absolute"]
        passes_adaptive = (max_val >= profile["ratio_threshold"] * bg_val
                           and max_val >= profile["ratio_min"])
        not_edge = x > 0 and y > 0

        if (passes_absolute or passes_adaptive) and not_edge:
            if not copied:
                work = work.copy()  # only pay for the copy on frames that hit
                copied = True
            if hot_mask is not None and hot_mask.is_hot(x, y):
                events.append((REJECTED_HOT, max_val, x, y, bg_val))
                work[max(0, y - hot_mask.radius):y + hot_mask.radius + 1,
                     max(0, x - hot_mask.radius):x + hot_mask.radius + 1] = 0
            elif is_spatially_valid(work, x, y, max_val):
                events.append((ACCEPTED, max_val, x, y, bg_val))
                h, w = work.shape
                work[max(0, y - hc):min(h, y + hc), max(0, x - hc):min(w, x + hc)] = 0
            else:
                events.append((REJECTED_SPATIAL, max_val, x, y, bg_val))
                work[max(0, y - 1):y + 2, max(0, x - 1):x + 2] = 0
        else:
            if max_val > profile["min_absolute"] * NEAR_MISS_FRACTION:
                events.append((REJECTED_SENSITIVITY, max_val, x, y, bg_val))
            break
    return events


def check_obscuration(gray_frame, avg_max, black_min):
    """Dual-gate lens-cover check. Returns (ok, avg_brightness, black_ratio).

    ok requires BOTH: mean brightness <= avg_max, and the fraction of pixels at
    gray <= OBSCURATION_BLACK_LEVEL to be >= black_min.
    """
    avg_brightness = float(np.mean(gray_frame))
    black_ratio = float(np.sum(gray_frame <= OBSCURATION_BLACK_LEVEL)) / gray_frame.size
    ok = avg_brightness <= avg_max and black_ratio >= black_min
    return ok, avg_brightness, black_ratio


def thermal_state(temp_c, soft_c, ceiling_c):
    """'HALT' above the ceiling, 'WARN' above the soft limit, else 'OK'."""
    if temp_c > ceiling_c:
        return "HALT"
    if temp_c > soft_c:
        return "WARN"
    return "OK"


class CalibrationGate:
    """Blocks detection until `frames` frames have been fed to the background
    model, so the first hits are not judged against an unsettled estimate."""

    def __init__(self, frames):
        self.frames = frames
        self.count = 0

    @property
    def done(self):
        return self.count >= self.frames

    def feed(self):
        """Counts one frame. Returns True on the frame that completes it."""
        if self.done:
            return False
        self.count += 1
        return self.done


class TraceLogger:
    """Rotating CSV of every candidate (accepted / rejected / near-miss) for
    offline threshold tuning. 2 MB per file, 5 backups (~12 MB worst case)."""

    # timestamp_iso is UTC (unchanged); local_time is the station's own clock with UTC offset.
    HEADER = "timestamp_iso,status,max_val,bg_val,ratio,loc_x,loc_y,note,local_time"

    def __init__(self, path, tz=None):
        self.tz = tz
        self._logger = logging.getLogger("TraceDiagnostics")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False  # keep it off the console stream
        for h in list(self._logger.handlers):
            self._logger.removeHandler(h)
            h.close()
        _retire_legacy_csv(path, self.HEADER)
        new_file = not os.path.exists(path) or os.path.getsize(path) == 0
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)
        if new_file:
            self._logger.info(self.HEADER)

    def log(self, status, max_val, bg_val, x, y, note=""):
        ratio = f"{max_val / bg_val:.2f}" if bg_val > 0 else "inf"
        ts = time.time()
        self._logger.info(
            f"{utc_iso(ts)},{status},{int(max_val)},{int(bg_val)},"
            f"{ratio},{x},{y},{note},{local_iso(ts, self.tz)}")


class LiveStats:
    """Hourly (configurable) log of how much the detector actually looked.

    Detection rates are only comparable between configurations if normalised by
    live time. The camera exposes frames at its own pace but the capture loop
    only *processes* some of them (buffer size 1, so stale frames are dropped),
    so the loop's frame count is the honest measure of what was observed:

        live fraction ~= frames * exposure_s / window_s     (verify the exposure
                                                             units with v4l2-ctl)

    One CSV row is appended per window (and on shutdown/halt, with the actual
    elapsed window_s), so the log survives restarts and outages.
    """

    HEADER = ("window_start_utc,window_start_local,window_s,frames,accepted,rejected_hot,rejected_spatial,"
              "near_miss,width,height,exposure_setting,gain_setting,exposure_actual,gain_actual,software_version")

    def __init__(self, path, window_s, width, height, exposure, gain, version, tz=None, read_controls=None):
        self.path = path
        self.tz = tz
        # exposure/gain are what we *requested*; read_controls() -> (exposure, gain) is what the
        # camera reports back at flush time (the camera may clamp or override a request).
        self.read_controls = read_controls
        _retire_legacy_csv(path, self.HEADER)
        self.window_s = window_s
        self.meta = f"{width},{height},{exposure},{gain}"
        self.version = version
        self.window_start = None   # set by start(); None = detection not active yet
        self.frames = 0
        self.counts = collections.Counter()

    def start(self, now):
        self.window_start = now
        self.frames = 0
        self.counts.clear()

    def frame(self):
        self.frames += 1

    def event(self, status):
        self.counts[status] += 1

    def maybe_flush(self, now):
        if self.window_start is not None and now - self.window_start >= self.window_s:
            self.flush(now)

    def flush(self, now):
        """Appends the current window as one CSV row, then starts a new window."""
        if self.window_start is None:
            return
        elapsed = now - self.window_start
        if elapsed < 1:
            return
        new_file = not os.path.exists(self.path) or os.path.getsize(self.path) == 0
        try:
            actual = self.read_controls() if self.read_controls else None
        except Exception:   # a camera/v4l2 hiccup must never cost us the row
            actual = None
        exp_actual, gain_actual = actual if actual else ("", "")
        row = (f"{utc_iso(self.window_start)},{local_iso(self.window_start, self.tz)},{elapsed:.0f},{self.frames},"
               f"{self.counts[ACCEPTED]},{self.counts[REJECTED_HOT]},{self.counts[REJECTED_SPATIAL]},"
               f"{self.counts[REJECTED_SENSITIVITY]},{self.meta},{exp_actual},{gain_actual},{self.version}")
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                if new_file:
                    f.write(self.HEADER + "\n")
                f.write(row + "\n")
            logging.info(f"[STATS] {elapsed:.0f}s window: {self.frames} frames "
                         f"({self.frames / elapsed:.1f}/s), {self.counts[ACCEPTED]} accepted, "
                         f"{self.counts[REJECTED_HOT]} hot, {self.counts[REJECTED_SPATIAL]} spatial")
        except OSError as e:
            logging.error(f"[STATS] Write Failed: {e}")
        self.start(now)


live_stats = None  # created under __main__; flushed on shutdown/halt

auth = AuthState()
environment = EnvironmentState()
store = LocalStore(PUBLIC_DIR)

# Bounded upload concurrency so a burst of hits can't spawn unbounded threads.
upload_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="upload")


def read_camera_controls(profile):
    """(exposure, gain) as the camera reports them now, as strings; ("", "") if unreadable."""
    v = f"/dev/video{CAMERA_INDEX}"
    exp_ctrl = "exposure_time_absolute" if profile['driver_quirk'] == "legacy" else "exposure_absolute"
    try:
        out = subprocess.run(["v4l2-ctl", "-d", v, f"--get-ctrl={exp_ctrl},gain"],
                             capture_output=True, text=True, timeout=5).stdout
        vals = dict(re.findall(r"(\w+):\s*(-?\d+)", out))
        return vals.get(exp_ctrl, ""), vals.get("gain", "")
    except (subprocess.SubprocessError, OSError):
        return "", ""


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
        exp_ctrl = "exposure_time_absolute" if profile['driver_quirk'] == "legacy" else "exposure_absolute"
        got = subprocess.run(["v4l2-ctl", "-d", v, f"--get-ctrl={exp_ctrl},gain"],
                             capture_output=True, text=True, timeout=5)
        logging.info(f"[HARDWARE] Camera reports: {' | '.join(got.stdout.split())}")
        exp_now, _ = read_camera_controls(profile)
        if exp_now and exp_now != str(profile['exposure']):
            logging.warning(f"[HARDWARE] Requested exposure {profile['exposure']} but the camera "
                            f"applied {exp_now}; live_stats.csv records both.")
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
    if live_stats is not None:
        live_stats.flush(time.time())
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


def halt(reason):
    """Critical stop: log it, publish it to status.json so the dashboard can
    show why, then exit non-zero. The program does not restart itself: whatever launched it
    (a service manager, or a cron watchdog running start_CMOSmicRay.sh) must do so, otherwise the
    station stays offline until started by hand or the Pi reboots."""
    logging.critical(reason)
    if live_stats is not None:
        live_stats.flush(time.time())
    store.status_extra["alert"] = reason
    store.update_status(bool(auth.get_token()), config, environment)
    sys.exit(1)


def verify_obscuration(gray, context):
    """Halts the process if the lens is not covered."""
    ok, avg, black = check_obscuration(gray, config.obscuration_avg_max, config.obscuration_black_min)
    if not ok:
        halt(f"[OBSCURATION] ABORT ({context}) -- lens does not appear covered: "
             f"avg_brightness={avg:.2f} (limit <= {config.obscuration_avg_max}), "
             f"black_ratio={black:.3f} (limit >= {config.obscuration_black_min}). "
             f"Cover the lens completely and restart.")


def check_thermal():
    cpu_temp = get_temp()
    store.status_extra["cpu_temp_c"] = cpu_temp
    state = thermal_state(cpu_temp, config.thermal_soft_c, config.thermal_ceiling_c)
    if state == "HALT":
        halt(f"[THERMAL] CPU {cpu_temp}C exceeds the {config.thermal_ceiling_c}C ceiling. "
             f"Halting; the process exits and must be restarted by its supervisor.")
    elif state == "WARN":
        logging.warning(f"[THERMAL] CPU {cpu_temp}C above soft limit {config.thermal_soft_c}C.")


if __name__ == "__main__":
    config = AppConfig.from_env(BASE_DIR)
    os.makedirs(config.public_dir, exist_ok=True)
    logging.info(f"--- CMOSmicRay {config.software_version} ---")
    environment.get_pressure(config.lat, config.lng)
    tune_driver(config.camera_profile_key, config.camera_profile)
    threading.Thread(target=ping, daemon=True).start()
    threading.Thread(target=store.writer_loop, daemon=True).start()

    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
    cap.set(3, config.capture_width); cap.set(4, config.capture_height); cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    signal.signal(signal.SIGINT, lambda sig, frame: shutdown(cap))
    signal.signal(signal.SIGTERM, lambda sig, frame: shutdown(cap))
    time.sleep(2)

    detector = MotionDetector()
    trace_logger = TraceLogger(os.path.join(BASE_DIR, "trace_diagnostics.csv"))
    calibration = CalibrationGate(config.calibration_frames)
    live_stats = LiveStats(os.path.join(BASE_DIR, "live_stats.csv"), config.stats_window_s,
                           config.capture_width, config.capture_height,
                           config.camera_profile["exposure"],
                           config.camera_profile["gain"], config.software_version,
                           read_controls=lambda: read_camera_controls(config.camera_profile))
    hot_mask = HotPixelMask(os.path.join(BASE_DIR, "hot_pixels.json"),
                            f"{config.capture_width}x{config.capture_height}",
                            config.hot_pixel_min_hits, config.hot_pixel_radius)
    logging.info(f"[CONFIG] Capture {config.capture_width}x{config.capture_height}; "
                 f"hot-pixel mask: {len(hot_mask._hot)} known position(s).")
    profile = config.camera_profile
    # Below this peak nothing can be accepted or even logged as a near-miss,
    # so skip the (comparatively costly) scan on the vast majority of frames.
    scan_floor = min(profile["ratio_min"], profile["min_absolute"] * NEAR_MISS_FRACTION)
    last_thermal_check = 0.0
    last_obscuration_check = time.time()
    store.status_extra.update(calibrating=True, calibration_progress=0,
                              calibration_frames=config.calibration_frames)

    while True:
        iter_start = time.time()
        ret, frame = cap.read()
        if not ret: time.sleep(1); continue
        compute_start = time.time()

        if iter_start - last_thermal_check >= config.thermal_check_interval:
            last_thermal_check = iter_start
            check_thermal()
            store.update_status(bool(auth.get_token()), config, environment)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        result = detector.detect(gray)
        if result is None:
            continue

        # Calibration gate: no detection until the background model has settled.
        if not calibration.done:
            just_finished = calibration.feed()
            if calibration.count % CALIBRATION_LOG_EVERY == 0:
                logging.info(f"[CAL] {calibration.count}/{config.calibration_frames} frames")
            store.status_extra["calibration_progress"] = calibration.count
            if just_finished:
                verify_obscuration(gray, "end of calibration")
                store.status_extra["calibrating"] = False
                store.update_status(bool(auth.get_token()), config, environment)
                last_obscuration_check = time.time()
                live_stats.start(last_obscuration_check)
                logging.info("[CAL] Calibration complete, lens verified covered. Detection active.")
            continue

        # Periodic sweep: catches the lens cap coming off mid-run.
        if iter_start - last_obscuration_check >= OBSCURATION_SWEEP_S:
            last_obscuration_check = iter_start
            verify_obscuration(gray, "periodic sweep")

        live_stats.frame()
        live_stats.maybe_flush(iter_start)
        max_val, x, y, diff = result
        events = scan_frame(diff, detector.bg_frame, profile, hot_mask=hot_mask) if max_val >= scan_floor else []
        compute_elapsed = time.time() - compute_start

        accepted = []
        for status, ev_val, ev_x, ev_y, ev_bg in events:
            trace_logger.log(status, ev_val, ev_bg, ev_x, ev_y)
            live_stats.event(status)
            if status == ACCEPTED:
                accepted.append((ev_val, ev_x, ev_y))
                hot_mask.record(ev_x, ev_y)
        if accepted:
            # Per-hit gate: never upload a hit if the lens is exposed right now.
            verify_obscuration(gray, "per-hit")
            for ev_val, ev_x, ev_y in accepted:
                process_hit(diff, ev_x, ev_y, ev_val)
            time.sleep(0.5)
        time.sleep(0.02)
        _record_loop_time(compute_elapsed, time.time() - iter_start)
