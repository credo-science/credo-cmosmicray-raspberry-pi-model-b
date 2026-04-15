# CMOSmicRay

![Network](https://img.shields.io/badge/Network-CREDO_Citizen_Science-blue)
![Status](https://img.shields.io/badge/Status-Production_v1.0-green)
![Platform](https://img.shields.io/badge/Platform-Raspberry_Pi-red)

**CMOSmicRay** is a production-grade cosmic ray detector client for the [CREDO Project](https://credo.science/). It transforms a standard webcam and a Raspberry Pi into a scientific instrument capable of detecting high-energy particle interactions (muons).

This software is optimized for **Legacy Hardware** (e.g., Raspberry Pi Model B) but runs perfectly on modern devices. It features automatic camera driver tuning, fault-tolerant network reporting, 64x64 pixel "Region of Interest" event cropping, a dynamic local monitoring dashboard, and **Live Configuration Swapping** for changing operators and settings on-the-fly without requiring a restart.

---

## 🛠️ Hardware Requirements

* **Computer:** Raspberry Pi (Model B, 2, 3, 4, or 5) running Raspberry Pi OS.
  <br>
  ![Raspberry Pi Model B](rpi_model_b.jpg)
* **Camera:** Logitech C270, C922, or similar UVC-compliant webcam.
* **Lens Cap:** The camera lens **must be completely blocked** (light-tight) using electrical tape or a printed cap.
* **Internet:** Wi-Fi or Ethernet connection for API uploads and Open-Meteo data fetching.

---

## 📊 Live Monitoring Dashboard

CMOSmicRay includes a beautifully designed, dark-mode responsive web dashboard. It continuously tracks the incoming cosmic ray flux and automatically analyzes the stream for statistically significant anomalies (using a rigorous baseline standard). 

The dashboard provides live flux-vs-pressure correlation graphs, lifetime detection statistics, recent particle track event captures, and dynamically pulls real-time environmental metrics (barometric pressure, outdoor temperature, and solar flare data) to display alongside your astronomical hits!

![CMOSmicRay Dashboard Preview](monitor_dashboard.JPG)

---

## 📦 Installation Instructions

Follow these steps to prepare your Raspberry Pi.

### Step 1: Update System
Ensure your package lists are up to date.
```bash
sudo apt-get update
sudo apt-get upgrade -y
```

### Step 2: Install System Dependencies
We use system-level packages for OpenCV and Video4Linux utilities. This is preferred over pip for Raspberry Pi to avoid long compilation times.
```bash
sudo apt-get install -y python3-opencv python3-numpy python3-requests python3-websockets v4l-utils
```
* **python3-opencv**: The computer vision library.
* **python3-websockets**: Required for the real-time Dashboard data feed.
* **v4l-utils**: Required for the script to auto-tune camera exposure settings (`v4l2-ctl`).

Alternatively, you can install the python packages via pip using the included `requirements.txt`:
```bash
pip3 install -r requirements.txt
```

### Step 3: Clone the Repository
Download the production code to your home directory.
```bash
cd ~
git clone https://github.com/credo-science/credo-cmosmicray-raspberry-pi-model-b.git
cd credo-cmosmicray-raspberry-pi-model-b
```

---

## ⚙️ Configuration

CMOSmicRay uses a `.env` file for security and configuration. 

### Step 1: Create your .env file
Configuration and credentials have moved from the shell script to a standard `.env` file in the `CMOSmicRay` directory. 

You **must** create a file named `.env` in the same directory as the script and provide your actual details. 

**Contents of your `.env` file:**
```bash
# --- USER CREDENTIALS ---
CREDO_USER="your_username"
CREDO_PASS="your_password"
CREDO_STATION="YourStationName"

# --- LOCATION CONFIGURATION ---

CREDO_LAT="YOUR_LATITUDE" 
CREDO_LNG="YOUR_LONGITUDE"
CREDO_ALT="YOUR_ALTITUDE"

# --- CAMERA SELECTION ---
CREDO_CAMERA_MODEL="C270"  # Options: "C270", "C922", or "Generic"

# --- HARDWARE & SOFTWARE CONFIGURATION ---
CREDO_DEVICE_MODEL="RPi Model B"
CREDO_SOFTWARE_VERSION="v1.3"
```

### Step 2: Customizing Variables
* `CREDO_USER`: Your registered CREDO project username.
* `CREDO_PASS`: Your CREDO project password.
* `CREDO_STATION`: The name you want to assign to this specific detection station.
* `CREDO_DEVICE_MODEL`: The hardware model (e.g., "RPi 4 Model B").
* `CREDO_LAT`, `CREDO_LNG`, `CREDO_ALT`: Your GPS coordinates.
* `CREDO_CAMERA_MODEL`: Choose between "C270" or "C922".

## 🚀 Usage

### Starting the Detector
To run the detector in the background (production mode), use the start script:
```bash
chmod +x start_CMOSmicRay.sh
./start_CMOSmicRay.sh
```

### Stopping the Detector
To safely shut down the background processes, release the camera hardware, and stop the dashboard server, use the stop script:
```bash
chmod +x stop_CMOSmicRay.sh
./stop_CMOSmicRay.sh
```

### The Dashboard
To view your detections locally:
1. Ensure the detection backend `CMOSmicRay.py` is running.
2. Serve the `CMOSmicRay` application folder locally:
```bash
cd ~/CMOSmicRay
python3 -m http.server 8000
```
3. Open the dashboard in your web browser. You can access it using any of the following methods:
   - **Working directly on the Raspberry Pi:** `http://127.0.0.1:8000/dashboard.html`
   - **From another computer (via Hostname):** `http://raspberrypi.local:8000/dashboard.html` (change `raspberrypi` if you renamed your host)
   - **From another computer (via IP Address):** `http://<raspberry-pi-ip>:8000/dashboard.html`

### Monitoring
You can watch the logs in real-time to verify detections and system health:
```bash
tail -f CMOSmicRay.log
```
**What to look for:**
* `[HARDWARE] Tuning driver...` -> Confirms camera settings are being applied.
* `[AUTH] Login Successful.` -> Confirms connection to CREDO API v2.
* `[NET] Ping Sent.` -> Confirms the 10-minute heartbeat.
* `[HIT] Energy: ...` -> Confirms a cosmic ray detection.

---

## 🧩 Technical Details

### Universal Driver Logic
The script includes a built-in Hardware Abstraction Layer. Based on the `CREDO_CAMERA_MODEL` variable, it automatically applies the optimal physics profile:

| Profile | Exposure | Threshold | Gain | Notes |
|---------|----------|-----------|------|-------|
| Logitech C270 | 1000 (Long) | 60 (Low) | Max | Optimized for small, noisy sensors. |
| Logitech C922 | 150 (Std) | 95 (High) | Med | Optimized for high-sensitivity 1080p sensors. |
| Generic | 500 | 80 | Med | Fallback for other webcams. |

### Data Privacy
This software only uploads **64x64 pixel crops** of detected particle events. It does not record or transmit full webcam video streams.

---

## ✍️ Authors / Credits
* **Jake Lee** - Original Author & Sole Developer (jakelee2029@gmail.com)

## 🤝 Contributing
Issues and Pull Requests are welcome! Please ensure any code changes maintain support for legacy ARMv6 architecture (Raspberry Pi 1/Zero).

## 📜 License
MIT License.
