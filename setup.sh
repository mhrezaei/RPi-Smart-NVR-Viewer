#!/bin/bash

# ==============================================================================
# RPi Smart NVR Viewer - Professional Installer
# ==============================================================================
# This script sets up the environment, installs dependencies in a virtual env,
# configures GPU memory, and sets up the application to auto-start on boot.
# ==============================================================================

# Ensure the script stops on errors
set -e

echo ""
echo ">>> Starting Smart NVR Viewer Installation..."
echo "---------------------------------------------"

# 1. Update System Repositories
# -----------------------------
echo "[1/7] Updating package lists..."
sudo apt-get update -y

# 2. Install System-Level Dependencies
# ------------------------------------
# vlc: The core media player engine
# libvlc-dev: Development headers required for python-vlc bindings
# python3-tk: The GUI framework (Tkinter)
# python3-venv: Required to create isolated Python environments (Fixes PEP 668 error)
# xscreensaver: Utility to easily disable screen blanking/sleep
echo "[2/7] Installing system libraries..."
sudo apt-get install -y vlc libvlc-dev python3-tk python3-pip python3-venv xscreensaver

# 3. Create Python Virtual Environment (VENV)
# -------------------------------------------
# This creates a folder named 'venv' in the current directory.
# All Python packages will be installed here, isolating them from the system.
echo "[3/7] Setting up Python Virtual Environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "    > Virtual environment created successfully in ./venv"
else
    echo "    > Virtual environment already exists. Skipping creation."
fi

# 4. Install Python Libraries inside VENV
# ---------------------------------------
# We explicitly call the 'pip' executable located INSIDE the venv folder.
echo "[4/7] Installing Python requirements into VENV..."
if [ -f "requirements.txt" ]; then
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt
    echo "    > Python dependencies installed successfully."
else
    echo "    ! ERROR: requirements.txt not found!"
    exit 1
fi

# 5. Configure Raspberry Pi GPU Memory (Direct File Edit)
# -----------------------------------------------------
# Instead of relying on raspi-config (which changes between versions),
# we edit config.txt directly.
echo "[5/7] Optimizing GPU Memory Split..."

# Detect config.txt location (It changed in newer RPi OS versions)
CONFIG_TXT=""
if [ -f "/boot/firmware/config.txt" ]; then
    CONFIG_TXT="/boot/firmware/config.txt"
elif [ -f "/boot/config.txt" ]; then
    CONFIG_TXT="/boot/config.txt"
fi

if [ -n "$CONFIG_TXT" ]; then
    # Check if gpu_mem is already defined
    if grep -q "^gpu_mem=" "$CONFIG_TXT"; then
        # Replace existing value
        sudo sed -i 's/^gpu_mem=.*/gpu_mem=256/' "$CONFIG_TXT"
        echo "    > Updated existing gpu_mem to 256MB in $CONFIG_TXT"
    else
        # Append new value
        echo "gpu_mem=256" | sudo tee -a "$CONFIG_TXT" > /dev/null
        echo "    > Added gpu_mem=256MB to $CONFIG_TXT"
    fi
else
    echo "    ! Warning: Could not find config.txt. Please set GPU memory to 256MB manually via raspi-config."
fi

# 6. Disable Screen Blanking (Optional but Recommended)
# ---------------------------------------------------
# Attempts to modify LXDE autostart to prevent the screen from going black.
echo "[6/7] Disabling Screen Blanking (Sleep Mode)..."
AUTOSTART_PATH="/etc/xdg/lxsession/LXDE-pi/autostart"
if [ -f "$AUTOSTART_PATH" ]; then
    # Grep checks if the line exists, if not, it appends it.
    grep -q "@xset s off" "$AUTOSTART_PATH" || sudo bash -c "echo '@xset s off' >> $AUTOSTART_PATH"
    grep -q "@xset -dpms" "$AUTOSTART_PATH" || sudo bash -c "echo '@xset -dpms' >> $AUTOSTART_PATH"
    grep -q "@xset s noblank" "$AUTOSTART_PATH" || sudo bash -c "echo '@xset s noblank' >> $AUTOSTART_PATH"
    echo "    > Screen blanking disabled in LXDE settings."
else
    echo "    > LXDE autostart file not found. Skipping global screen config (App will handle it locally)."
fi

# 7. Create Autostart Entry
# -------------------------
# Creates a .desktop file that tells the OS to run our app on boot.
# CRITICAL: It uses the absolute path to the VENV python interpreter.
echo "[7/7] Configuring Autostart..."
mkdir -p /home/$USER/.config/autostart

# Get absolute paths
APP_DIR=$(pwd)
VENV_PYTHON="$APP_DIR/venv/bin/python"
MAIN_SCRIPT="$APP_DIR/main.py"

# Write the .desktop file
cat <<EOF > /home/$USER/.config/autostart/nvr-viewer.desktop
[Desktop Entry]
Type=Application
Name=Smart NVR Viewer
Comment=Auto-start NVR Viewer in Kiosk Mode
Exec=$VENV_PYTHON $MAIN_SCRIPT
WorkingDirectory=$APP_DIR
StartupNotify=false
Terminal=false
X-KeepSoftware=true
EOF

echo "    > Autostart entry created at /home/$USER/.config/autostart/nvr-viewer.desktop"

echo ""
echo "========================================================"
echo "   INSTALLATION COMPLETE SUCCESSFULL!"
echo "========================================================"
echo "1. Your Python environment is set up in './venv'"
echo "2. The app is configured to start automatically on boot."
echo ""
echo "IMPORTANT: Please REBOOT your Raspberry Pi now."
echo "Command: sudo reboot"
echo "========================================================"
