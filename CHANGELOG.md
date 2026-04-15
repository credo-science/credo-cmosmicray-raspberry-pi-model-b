# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
