## 📹 RPi Smart NVR Viewer

**A robust, hardware-accelerated Kiosk Mode RTSP viewer designed for 24/7 monitoring on Raspberry Pi.**

Turn your Raspberry Pi into a dedicated security monitor. This application connects to any NVR or IP Camera via RTSP, plays the stream with low latency using VLC, and automatically heals itself if the network drops.

## ✨ Features

**🖥️ Kiosk Mode:** Boots directly into full-screen without a mouse cursor or desktop distractions.

## **🛡️ Self-Healing:**

      * Auto-detects network interruptions (Ping check).

      * Auto-detects frozen video streams (VLC state check).

      * Automatically reconnects without user intervention.

  **⚙️ Admin Dashboard:** Hidden settings panel (`Ctrl + Alt + S`) to change IP, credentials, or view system stats.

  **📊 System Health:** Monitor CPU, RAM, Temperature, and Uptime directly from the app.

## **🚀 Optimized Performance:**

      * Uses TCP transport to prevent image artifacts (gray screens).

      * Optimized VLC caching for low latency.

      * Prevent screen sleeping/blanking automatically.

  **⚡ Easy Setup:** Includes an automated installer script.



## 🛠️ Hardware Requirements

  * **Raspberry Pi 3B+ / 4 / 5** (Recommended for smooth decoding).

  * **OS:** Raspberry Pi OS (Desktop version required for Tkinter/VLC GUI).

  * **Network:** Ethernet connection is highly recommended for stable RTSP streaming.


## 🚀 Installation

### Option 1: Quick Install (Recommended)

1.  Open a terminal on your Raspberry Pi.

2.  Clone the repository and run the setup script:

## <!-- end list -->

```bash

git clone [https://github.com/mhrezaei/RPi-Smart-NVR-Viewer.git](https://github.com/mhrezaei/RPi-Smart-NVR-Viewer.git)

cd RPi-Smart-NVR-Viewer

chmod +x setup.sh

./setup.sh

```

## **What the script does:**

  * Installs system dependencies (`vlc`, `python3-tk`, `xscreensaver`).

  * Installs Python libraries (`python-vlc`, `psutil`).

  * **Crucial:** Increases GPU Memory split to **256MB** (required for smooth video).

  * Creates an **Autostart** entry so the viewer launches on boot.

### Option 2: Manual Installation

## If you prefer to install manually:

```bash

# 1. Update System

sudo apt-get update

# 2. Install Dependencies

sudo apt-get install -y vlc libvlc-dev python3-tk python3-pip xscreensaver

# 3. Install Python Packages

pip3 install -r requirements.txt

# 4. Optimize GPU Memory (Essential for video!)

sudo raspi-config nonint do_memory_split 256

```


## ⚙️ Configuration

The application uses a `nvr_config.json` file. You can edit this file manually or use the built-in **Admin Dashboard**.

## **Default Configuration:**

```json
{
"nvr_ip": "192.168.1.108",
"nvr_port": "554",
"nvr_user": "view",
"nvr_pass": "viewer123456",
"channel": "0",
"subtype": "0",
"admin_pass": "admin",
"ping_interval": 5,
"network_caching": 600
}
```


## 🎮 Usage & Controls

## Once the application is running:

  * **View Stream:** The stream loads automatically.

  * **Admin Panel:** Press `Ctrl + Alt + S` and enter the admin password (default: `admin`).

  * **Refresh Stream:** Click the hidden button in the **bottom-right corner** (or use the Admin Panel).

  * **Exit App:** Press `Esc` (Note: In production, you might want to remove this binding from the code).

## 📸 Screenshots

## *(Add screenshots of your UI here)*

## 🔧 Troubleshooting

## **1. The screen is black / No video:**

  * Check if the NVR IP and credentials are correct in `nvr_config.json`.

  * Verify the RTSP stream URL format using a desktop VLC player: `rtsp://user:pass@ip:554/cam/realmonitor?channel=0&subtype=0`

## **2. Video is lagging or gray artifacts:**

  * Ensure you have increased GPU memory to 256MB (`sudo raspi-config`).

  * Ensure you are using Ethernet, not WiFi.

  * Try increasing `network_caching` in the config to `1000`.

## **3. "Required libraries not found" error:**

  * Run `pip3 install -r requirements.txt` again.

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.

## 👨‍💻 Author

## **Mohammad Hadi Rezaei**

## *Made with ❤️ for the Raspberry Pi Community.*
