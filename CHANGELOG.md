# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.7.5] - 2026-11-16
Covers the 1.7.x series (1.7.1 – 1.7.5). Builds on the refactored, tested code base from `f8d7f2f`, which had dropped the safety and filtering features of 1.3–1.6; those are restored here.

### Added
- **Safety gates restored in the refactored code**: 500-frame calibration gate, dual-condition lens-cover check (end of calibration, every 600 s, and before every upload), tiered thermal protection (warn 60 °C, halt 70 °C), 3×3 spatial filter, adaptive thresholds (`min_absolute`, `ratio_threshold`, `ratio_min`) and up to 3 traces per frame. All limits are configurable from `.env` (see `.env.example`).
- **Hot-pixel mask**: a position that yields 5 accepted hits (`HOT_PIXEL_MIN_HITS`) is treated as a sensor defect and every candidate within 3 px (`HOT_PIXEL_RADIUS`) is rejected as `REJECTED_HOT`. Positions are kept per capture size in `hot_pixels.json`. Long-run data showed fixed hot spots producing 65 % of accepted hits at 1280×720 against 1 % at 640×480.
- **Live-time log**: `live_stats.csv` gets one row per hour (`STATS_WINDOW_S`) with frames processed, hits by status, capture size, requested and camera-reported exposure/gain and software version, so detection rates can be given per live hour.
- **Local timestamps**: `trace_diagnostics.csv` and `live_stats.csv` keep UTC and add a station-local column with its UTC offset. The station timezone is detected (`STATION_TZ`, `/etc/timezone`, `/etc/localtime`) and published in `status.json`; the dashboard shows station-local time. A CSV with an older column layout is moved to `*.legacy-<timestamp>.csv` instead of being mixed with the new layout.
- **Permanent archive and trace log** (`detections_archive.jsonl`, `trace_diagnostics.csv`), kept outside `public/` so the dashboard web server does not serve them.
- **Dashboard** (now `public/dashboard.html`): station-local clock and day buckets, CPU temperature, and a banner when the station is halted or not reporting.
- **Tooling**: 74 pytest tests, a GitHub Actions workflow, `requirements.txt` / `requirements-dev.txt`, a `logrotate` rule (`packaging/`), and README sections on the safety gates, settings, output files and a cron watchdog.

### Changed
- **Default capture size is 640×480** (`CAPTURE_WIDTH`, `CAPTURE_HEIGHT`). Expect about 6 processed frames/s on a Raspberry Pi Model B, against about 2 at 1280×720.
- **C270 profile requests exposure 660.** The camera clamps anything above 666 while streaming, so the earlier request of 1000 was never applied.
- **Start and stop scripts** match the detector and the dashboard server by the start of their command line, so a shell or ssh session that merely mentions the file name is no longer mistaken for the detector. The dashboard server serves only `public/`, and credentials are read from `.env`.
- A halt (lens uncovered, CPU above the limit) writes its reason to `status.json` and exits with status 1. **The program does not restart itself**; use a service manager or the cron watchdog described in the README.

### Fixed
- `update_status` no longer blocks the capture loop on a weather request, and writes `status.json` atomically.
- Heartbeat `on_time` is the time since the last successful ping (CREDO sums it server-side).

### Removed (relative to 1.6.0 / `de70d2e`)
These were already absent from the Oct 2026 refactor and are not restored:
- The WebSocket live feed; the dashboard polls `detections.json` and `status.json` instead.
- The `.env` live-reload watcher; restart the detector after changing `.env`.
- The 1.5.0 wording change: log messages say "Energy" again rather than "Intensity" (the value is a peak grey-level excess, not an energy).

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
