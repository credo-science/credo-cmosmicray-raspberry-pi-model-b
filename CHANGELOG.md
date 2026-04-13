# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-04-12
### Added
- **Live Configuration Watcher**: Added a background daemon in `CMOSmicRay.py` that actively monitors `.env` for changes and triggers real-time credential swaps (`CREDO_USER`) and API re-authentication without interrupting the detection stream or requiring a restart.
- **No Countdown/Operating Time & Live Temp/Pressure Graph**: The current dashboard.html does feature a live Uptime ticker (<span id="sys-uptime">) driven by a JS WebWorker that updates every second. Furthermore, there is a live <canvas id="muonChart"> that actively polls WebSockets every 1 second to draw a continuous graph of Temperature and Pressure even when no particle hits are detected.
- **No Option to Zoom in on the Image**: The dashboard.html currently has code so that if a user double-clicks an image in the gallery (div.ondblclick = () => openModal(src);), it expands into a full-screen zoom modal.

### Fixed
- **Dashboard Flickering Bug**: Resolved a race condition where the dashboard's javascript would incorrectly parse `detections.json` (historical metadata) and overwrite the top-right header, causing it to conflict with the real-time live connection state inside `status.json`.

### Changed
- **Documentation**: Expanded the README to include proper instructions for terminating the daemon (`stop_CMOSmicRay.sh`) and clearer networking examples for remote dashboard viewing (including `127.0.0.1` vs `hostname` interactions).
