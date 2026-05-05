# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
