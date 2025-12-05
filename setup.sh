#!/bin/bash

# RPi Smart NVR Viewer - Auto Installer
# -------------------------------------

echo ">>> Starting Installation..."

# 1. Update Repository
echo "[1/5] Updating package lists..."
sudo apt-get update -y

# 2. Install System Dependencies
# vlc: The media player engine
# libvlc-dev: Development headers
# python3-tk: GUI framework
# xscreensaver: To manage screen blanking easily
echo "[2/5] Installing system libraries..."
sudo apt-get install -y vlc libvlc-dev python3-tk python3-pip xscreensaver

# 3. Install Python Libraries
echo "[3/5] Installing Python requirements..."
pip3 install -r requirements.txt

# 4. Configure GPU Memory (Split)
# Raspberry Pi needs more GPU RAM to decode video smoothly.
# We check if 'raspi-config' exists and set memory to 256MB.
echo "[4/5] Optimizing GPU Memory..."
if command -v raspi-config > /dev/null; then
    sudo raspi-config nonint do_memory_split 256
    echo "    GPU Memory set to 256MB."
else
    echo "    Warning: 'raspi-config' not found. Ensure GPU memory is at least 128MB manually."
fi

# 5. Create Autostart Entry
# This ensures the app runs automatically when the Desktop loads.
echo "[5/5] Setting up Autostart..."
mkdir -p /home/$USER/.config/autostart
cat <<EOF > /home/$USER/.config/autostart/nvr-viewer.desktop
[Desktop Entry]
Type=Application
Name=Smart NVR Viewer
Exec=python3 $(pwd)/main.py
StartupNotify=false
Terminal=false
EOF

echo "------------------------------------------------"
echo "Installation Complete!"
echo "Please REBOOT your Raspberry Pi to apply changes."
echo "Command: sudo reboot"
echo "------------------------------------------------"
