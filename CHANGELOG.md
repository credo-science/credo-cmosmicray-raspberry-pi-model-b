# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.7.5] - 2026-11-18
Deployed on the station on 15 Nov 2026; updated on 18 Nov 2026 following code review to restore full v1.2–v1.6 feature parity and add automatic lens-cover recovery.

### Added
- **Automatic Lens-Cover Recovery (Auto-Resume) & 2-Line Critical Dashboard Alerts**: Uncovering the camera lens now logs the two `CRITICAL` messages (`[OBSCURATION] FAIL ...` with `avg_brightness` and `black_density per mille`, and `[OBSCURATION] PAUSED ...`), publishes both lines in `status.json` (`alerts`), renders them in a prominent banner at the top of `public/dashboard.html`, and pauses detection. Once the lens is covered again, the detector automatically resets its background model, runs a fresh 500-frame calibration, clears the alert banner, and resumes detection without requiring a manual restart.
- **Routine CPU Temperature Logging & Sidebar Readout**: `check_thermal()` now logs a `[THERMAL] CPU temperature: XX.X°C (soft limit 60.0°C, ceiling 70.0°C)` info message at startup and every 5 minutes (`THERMAL_LOG_INTERVAL = 300`), falls back to `/sys/class/thermal/thermal_zone0/temp` when `vcgencmd` is unavailable, and displays CPU temperature explicitly in both the topbar and the Environmental sidebar (`🖥 CPU Temp:`).
- **Live Environmental Telemetry Graph, Uptime Ticker & `.env` Live-Reload Watcher Restored**:
  - `LocalStore.record_telemetry()` maintains a rolling history (`env_history` in `status.json`) of barometric pressure and temperature, driving the dual-axis **Live Environmental Telemetry (Temperature & Pressure)** chart (`#envChart`) via HTTP polling (no `websockets` daemon required).
  - Restored `EnvWatcher` to monitor `.env` every 2 seconds and reload `AppConfig` live (re-authenticating when credentials or station ID change, while safely ignoring partial/invalid edits).
  - Restored the live `UPTIME` counter (`#sys-uptime`), the v1.5.0 `❓ Help` (`#infoModal`) and `⚙️ Settings` (`#settingsModal`) modals, and the `"Intensity (ADC)"` terminology across logs and the dashboard.

### Changed
- **C270 profile requests exposure `660` (`1325db7`, 2026-11-15)**: Diagnostic readback from v1.7.4 showed the Logitech C270 UVC driver clamps `exposure_time_absolute` at `666` (`66.6 ms`) while streaming at `640×480` regardless of requested frame rate, so the earlier setting of `1000` was never applied.
- **Start and stop scripts (`a0511ee`, 2026-11-16)**: Match the detector and dashboard processes by the start of their command line so an `ssh` or shell session mentioning `CMOSmicRay.py` is never mistaken for the running detector.

### Fixed
- **Restored `"Outdoor"` temperature label on the dashboard**: Fixed `updateWeather()` overwriting the `"Outdoor"` label with `"Station"` (`🌡 Outdoor: XX.X°C (XX.X°F)`).
- **Fixed alert banner positioning**: Moved `#station-banner` inside `<main class="main-content">` so it is never obscured behind the fixed `.topbar` header.

---

## [1.7.4] - 2026-11-15
Deployed on the station on 15 Nov 2026 (`8e59794`).

### Added
- **Camera-Reported Exposure & Gain Verification**: Added `read_camera_controls()` (`v4l2-ctl --get-ctrl`) to query the actual exposure and gain applied by the camera driver at startup and at each hourly `LiveStats` flush. `live_stats.csv` now records both `exposure_setting,gain_setting` (requested) and `exposure_actual,gain_actual` (reported by hardware), logging a warning if the camera clamps the requested value.

---

## [1.7.3] - 2026-11-15
Deployed on the station on 15 Nov 2026 (`21e87cc`, `80f16b4`).

### Added
- **Hourly Live-Time Log (`live_stats.csv`)**: Added `LiveStats` to append one row per hour (`STATS_WINDOW_S`, default `3600` s, plus partial-window flush on shutdown/halt) recording processed frame count, event counts by gate (`accepted`, `rejected_hot`, `rejected_spatial`, `near_miss`), capture resolution, exposure/gain, and software version so detection rates can be normalized per live hour of sensor integration.
- **Station-Local Timestamps**: `trace_diagnostics.csv` and `live_stats.csv` retain their UTC ISO-8601 column and add a station-local timestamp column with explicit UTC offset (e.g., `2026-11-15T19:26:21.123-05:00`). Station timezone is auto-detected (`STATION_TZ`, `/etc/timezone`, `/etc/localtime`) and published in `status.json`. Existing CSVs with an older header schema are automatically retired to `*.legacy-<timestamp>.csv`.
- **CI & Packaging Tooling**: Added GitHub Actions test workflow (`.github/workflows/tests.yml`), `requirements.txt` / `requirements-dev.txt`, and `packaging/cmosmicray.logrotate`.

---

## [1.7.2] - 2026-11-15
Deployed on the station on 15 Nov 2026 (`dc8887d`).

### Added
- **Persistent Hot-Pixel Mask (`HotPixelMask`, `hot_pixels.json`)**: Learns fixed-position sensor defects online from accepted hits. Any pixel coordinate that accumulates `HOT_PIXEL_MIN_HITS` (default `5`) accepted hits is marked hot, and subsequent candidates within `HOT_PIXEL_RADIUS` (default `3` px, a `7×7` neighborhood) are rejected as `REJECTED_HOT`. Masks are persisted in `hot_pixels.json` keyed by capture resolution (`"640x480"`, `"1280x720"`). Motivated by analysis of the 80.4-day pre-v1.7 diagnostic log (`trace_diagnostics_pre_v1.7.csv`), where fixed hot spots accounted for 65% of accepted hits at `1280×720` vs. 1% at `640×480`.

### Changed
- **Default Capture Resolution Set to `640×480`** (`CAPTURE_WIDTH`, `CAPTURE_HEIGHT`, configurable via `.env`): Matches the resolution the station had been running locally since 11 May 2026 and committed in `de70d2e` (20 May 2026), yielding ~6.0 processed frames/s on a Raspberry Pi Model B vs. ~2.1 frames/s at `1280×720`.

---

## [1.7.1] - 2026-11-15
Internal milestone (`dc8887d`) reconciling the v1.3–v1.6 safety and filtering gates with the October 2026 modular refactor.

### Added
- **Restored v1.3–v1.6 Safety & Filtering Pipeline into Modular Architecture**:
  - 500-frame startup `CalibrationGate` (v1.4.0)
  - Dual-condition lens-cover obscuration check (`check_obscuration`: end of calibration, every 600 s periodic sweep, and per-hit before upload; v1.4.0–v1.6.0)
  - Two-tier CPU thermal protection (`thermal_state`: soft warning at `60.0°C`, critical halt at `70.0°C`; v1.4.0, v1.6.0)
  - 3×3 spatial neighbor filter (`is_spatially_valid`; v1.3.0)
  - Adaptive and absolute sensitivity thresholds (`min_absolute`, `ratio_threshold`, `ratio_min`; v1.3.0)
  - Multi-trace iterative frame scanning (up to 3 traces per frame with 64×64 ROI masking; v1.3.1)
  - Rotating diagnostic trace logger (`TraceLogger` -> `trace_diagnostics.csv`; v1.3.0) and crash-safe append-only detection archive (`detections_archive.jsonl`, kept outside `public/`; `de70d2e`)
- **Expanded Automated Test Suite (`tests/test_gates.py`)**: Added unit tests covering all safety gates, spatial filtering, multi-trace extraction, hot-pixel masking, CSV schema migration, and atomic status updates (bringing the test suite to 77 tests).

---

## [Refactor / 1.6.1-dev] - 2026-10-30 (`f8d7f2f`)
*Note on version history:* This modularity and testability refactor encapsulated module-level globals into testable classes (`AppConfig`, `AuthState`, `EnvironmentState`, `LocalStore`, `MotionDetector`), introduced a single-writer thread queue for `detections.json`, bounded upload concurrency (`ThreadPoolExecutor`), corrected CREDO heartbeat `on_time` to send interval `delta`, moved web assets into `public/`, and added the initial `pytest` suite (`test_auth.py`, `test_config.py`, `test_local_log.py`, `test_motion_detector.py`, `test_process_hit.py`).

However, because the refactor branch had been started from an earlier pre-v1.3 snapshot of `CMOSmicRay.py` rather than `de70d2e` (v1.6.0), merging `f8d7f2f` unintentionally omitted the v1.3–v1.6 safety gates, WebSocket telemetry graph, and `.env` watcher. Those omitted features were subsequently audited and restored in **v1.7.1 – v1.7.5**.

## [1.6.0] - 2026-05-05
### Added
- **Per-Hit Obscuration Gate (Gold Standard)**: `check_obscuration()` now runs inside `process_hit()` before any upload is triggered. If the lens is exposed at the moment of detection, the hit is discarded and the process halts immediately — guaranteeing zero junk data ever reaches the CREDO API.
- **Tiered Thermal Protection**: Added a `60°C` soft-warning tier (logs `WARNING`, keeps running) below the existing halt tier. The halt threshold is now user-configurable via `CREDO_THERMAL_CEILING` in `.env` (default: `70.0`), enabling operators on older/hotter hardware to tune it without touching source code.

## [1.5.0] - 2026-05-02
### Added
- **Periodic Obscuration Check**: The system now runs a mid-run verification every 10 minutes to ensure the lens is still completely covered. If exposed, it aborts the process to protect the data stream.
- **Scientific Terminology**: Renamed "Energy" to "Intensity" across logging and the UI to accurately reflect peak grayscale brightness (0-255).
- **Dashboard Info Modal**: Added an interactive "Help" (`?`) modal to the dashboard to explain Grayscale Intensity, SCFA Sigma Levels, and Thermal/Obscuration Safety Guards.

## [1.4.0] - 2026-05-02
### Added
- **Pre-Detection Calibration Phase**: Added a hard-block calibration gate that accumulates exactly 500 frames before enabling detection. Progress is logged every 100 frames (`[CAL]`). The `broadcast()` WebSocket payload now includes `calibrating` and `calibration_progress` fields so the dashboard can surface startup state.
- **Sensor Obscuration Verification**: Added `check_obscuration()` which enforces both PRD thresholds on the final calibration frame — average brightness ≤ 10 and black pixel density ≥ 999 per mille. If the lens is not covered, the process aborts with `sys.exit(1)` and a `CRITICAL` log message.
- **Thermal Protection Guard**: Wired the existing `get_temp()` (vcgencmd) into the main detection loop with a 30-second polling interval. If CPU temperature exceeds 70°C, the process exits via `sys.exit(1)`, allowing systemd to perform an automatic restart.

## [1.3.1] - 2026-04-20
### Added
- **Multi-Trace Iterative Detection**: Enhanced the core detection loop to scan each frame for up to 3 independent particle traces. After identifying a peak, the system now "blacks out" the 64x64 region and re-runs the analysis to catch simultaneous cosmic ray events.
- **Batched Cooldown Logic**: Refactored the 500ms API protection sleep to occur once per frame *after* all potential hits are processed, maximizing temporal resolution while maintaining system stability.


## [1.3.0] - 2026-04-14
### Added
- **Diagnostic Trace Logger**: Implemented a rotating CSV logger (`trace_diagnostics.csv`) that securely records max value, background brightness, and ratios for accepted hits, spatial-rejections, and near-misses for future tuning.
- **Adaptive Detection Thresholds**: Introduced `min_absolute`, `ratio_threshold`, and `ratio_min` to the camera profiles to automatically scale sensitivity based on dynamic sensor background levels.
- **Spatial Hardening Check**: Added a 3x3 neighbor analysis (`is_spatially_valid`) to eliminate false positives caused by single isolated "hot pixels".

### Fixed
- **WebSocket Thread Concurrency**: Elevated `handler` and `broadcast` to the module level to fix scope issues, added explicit `asyncio.set_event_loop(new_loop)` for Python 3.10+ compatibility, and enabled `ConnectionClosed` catching to prevent zombie connections from accumulating and crashing the background loop.
- **Broadcast Guard**: Implemented robust exception handling with `ping_interval=20` and `ping_timeout=20` to instantly prune disconnected clients.
- **Memory Leaks**: Capped `data_cache` strictly at 1000 items (using a standard JSON-serializable list rather than infinite deque) to keep the Raspberry Pi memory stable.

## [1.2.0] - 2026-04-12
### Added
- **Live Configuration Watcher**: Added a background daemon in `CMOSmicRay.py` that actively monitors `.env` for changes and triggers real-time credential swaps (`CREDO_USER`) and API re-authentication without interrupting the detection stream or requiring a restart.
- **No Countdown/Operating Time & Live Temp/Pressure Graph**: The current dashboard.html does feature a live Uptime ticker (<span id="sys-uptime">) driven by a JS WebWorker that updates every second. Furthermore, there is a live <canvas id="muonChart"> that actively polls WebSockets every 1 second to draw a continuous graph of Temperature and Pressure even when no particle hits are detected.
- **No Option to Zoom in on the Image**: The dashboard.html currently has code so that if a user double-clicks an image in the gallery (div.ondblclick = () => openModal(src);), it expands into a full-screen zoom modal.

### Fixed
- **Dashboard Flickering Bug**: Resolved a race condition where the dashboard's javascript would incorrectly parse `detections.json` (historical metadata) and overwrite the top-right header, causing it to conflict with the real-time live connection state inside `status.json`.

### Changed
- **Documentation**: Expanded the README to include proper instructions for terminating the daemon (`stop_CMOSmicRay.sh`) and clearer networking examples for remote dashboard viewing (including `127.0.0.1` vs `hostname` interactions).
