#!/usr/bin/env python3
"""
Smart NVR Viewer for Raspberry Pi
---------------------------------
A robust, kiosk-mode RTSP stream viewer designed for 24/7 monitoring.
Features include auto-reconnection, health monitoring, and a hidden admin dashboard.

Author: Your Name
License: MIT
"""

import sys
import os
import time
import json
import threading
import subprocess
import tkinter as tk
from tkinter import simpledialog, messagebox, Toplevel, Label, Entry, Button
from datetime import datetime

# ==========================================
# DEPENDENCY CHECK & GUI ERROR HANDLING
# ==========================================
# We wrap imports in a try-block to catch 'externally-managed-environment' issues
# or missing venv activations, displaying a visible error on the screen.
try:
    import vlc
    import psutil
except ImportError as e:
    # Initialize a minimal Tkinter instance just to show the error
    root = tk.Tk()
    root.withdraw() # Hide the main window
    error_msg = (
        f"CRITICAL ERROR: Missing Dependencies.\n\n"
        f"Python could not find required libraries (vlc, psutil).\n"
        f"Details: {e}\n\n"
        f"SOLUTION:\n"
        f"This app requires a Virtual Environment (venv) on newer Raspberry Pi OS.\n"
        f"Please run the 'setup.sh' script again to fix this."
    )
    messagebox.showerror("Startup Error", error_msg)
    sys.exit(1)

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
CONFIG_FILE = "nvr_config.json"
DEFAULT_CONFIG = {
    "nvr_ip": "192.168.1.108",
    "nvr_port": "554",
    "nvr_user": "admin",
    "nvr_pass": "admin123",
    "channel": "0",       # 0 = Zero Channel (Overview), 1+ = Specific Camera
    "subtype": "0",       # 0 = Main Stream, 1 = Sub Stream
    "admin_pass": "admin",
    "ping_interval": 5,   # Seconds between network checks
    "network_caching": 600 # VLC Caching in ms (Higher = smoother, more latency)
}

class SmartNVRApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Smart NVR Viewer")
        self.root.configure(bg="black")
        
        # --- Load Configuration ---
        self.config = self.load_config()
        
        # --- System Optimization ---
        # Prevent screen sleep/blanking
        self.disable_screensaver()
        
        # --- UI Setup (Kiosk Mode) ---
        self.root.attributes('-fullscreen', True)
        self.root.config(cursor="none") # Hide mouse cursor
        
        # Video Container Frame
        self.video_frame = tk.Frame(self.root, bg="black")
        self.video_frame.pack(fill=tk.BOTH, expand=True)
        
        # Status Overlay (Center Screen Message)
        self.status_label = tk.Label(
            self.root, 
            text="Initializing System...", 
            font=("Arial", 24, "bold"), 
            fg="#00ff00", 
            bg="black",
            wraplength=800
        )
        self.status_label.place(relx=0.5, rely=0.5, anchor="center")
        
        # Refresh Button (Bottom Right - Semi-hidden)
        self.btn_refresh = tk.Button(
            self.root, text="⟳", font=("Arial", 16),
            bg="#222", fg="#555", bd=0, command=self.restart_stream
        )
        self.btn_refresh.place(relx=0.98, rely=0.98, anchor="se")

        # --- VLC Player Setup ---
        # Arguments optimized for Raspberry Pi (MMAL/ALSA) and low latency
        vlc_args = [
            "--no-xlib", 
            f"--network-caching={self.config.get('network_caching', 600)}",
            "--rtsp-tcp" # Force TCP to avoid UDP packet loss artifacts
        ]
        self.vlc_instance = vlc.Instance(*vlc_args)
        self.player = self.vlc_instance.media_player_new()
        
        # Embed VLC into Tkinter Window
        self.embed_player()

        # --- State Variables ---
        self.running = True
        self.network_ok = False
        self.last_error = ""

        # --- Input Bindings ---
        # Ctrl + Alt + S: Open Admin Settings
        self.root.bind("<Control-Alt-s>", self.open_admin_panel)
        # ESC: Emergency Exit
        self.root.bind("<Escape>", self.on_close)

        # --- Start Background Monitoring ---
        # Using a separate thread to prevent GUI freezing during Ping/Connect
        self.monitor_thread = threading.Thread(target=self.system_monitor_loop, daemon=True)
        self.monitor_thread.start()

    def load_config(self):
        """Loads configuration from JSON file or creates default."""
        if not os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(DEFAULT_CONFIG, f, indent=4)
            except Exception as e:
                print(f"Error creating config: {e}")
            return DEFAULT_CONFIG.copy()
        else:
            try:
                with open(CONFIG_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error reading config: {e}")
                return DEFAULT_CONFIG.copy()

    def save_config(self):
        """Saves current configuration to JSON file."""
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save config: {e}")

    def disable_screensaver(self):
        """Disables Linux screen blanking using xset (if available)."""
        if sys.platform.startswith('linux'):
            try:
                os.system("xset s off")      # Disable screensaver
                os.system("xset -dpms")      # Disable energy saving
                os.system("xset s noblank")  # Disable blanking
            except Exception:
                pass # Fail silently if xset is missing

    def embed_player(self):
        """Connects the VLC player to the Tkinter Frame ID."""
        # This is critical. Without this, VLC opens a separate window.
        if sys.platform.startswith('linux'):
            self.player.set_xwindow(self.video_frame.winfo_id())
        elif sys.platform == "win32":
            self.player.set_hwnd(self.video_frame.winfo_id())

    def build_rtsp_url(self):
        """Constructs the RTSP URL based on config credentials."""
        # Template: rtsp://user:pass@ip:port/cam/realmonitor?channel=x&subtype=y
        return (f"rtsp://{self.config['nvr_user']}:{self.config['nvr_pass']}@"
                f"{self.config['nvr_ip']}:{self.config['nvr_port']}/"
                f"cam/realmonitor?channel={self.config['channel']}&subtype={self.config['subtype']}")

    def update_ui_message(self, text, color="white", show=True):
        """Thread-safe method to update the overlay message."""
        def _update():
            if show:
                self.status_label.config(text=text, fg=color)
                self.status_label.lift()
            else:
                self.status_label.lower()
        self.root.after(0, _update)

    def check_ping(self):
        """Pings the NVR IP. Returns True if reachable."""
        ip = self.config["nvr_ip"]
        try:
            # -c 1: count 1, -W 1: wait 1 sec timeout
            param = '-n' if sys.platform.lower()=='win32' else '-c'
            output = subprocess.run(
                ["ping", param, "1", ip], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE
            )
            return output.returncode == 0
        except Exception:
            return False

    def start_stream(self):
        """Initializes VLC media and starts playback."""
        url = self.build_rtsp_url()
        media = self.vlc_instance.media_new(url)
        self.player.set_media(media)
        self.player.play()

    def restart_stream(self):
        """Manual restart trigger."""
        self.player.stop()
        time.sleep(0.5)
        self.start_stream()

    def system_monitor_loop(self):
        """
        Main Watchdog Loop:
        1. Checks Network Connectivity (Ping)
        2. Checks Video Player Status
        3. Handles Reconnection Logic
        """
        while self.running:
            # Step 1: Network Check
            if self.check_ping():
                if not self.network_ok:
                    self.network_ok = True
                    self.update_ui_message("Network Restored.\nConnecting to NVR...", "#00ff00")
                    self.restart_stream()
                    time.sleep(2) # Give VLC time to initialize
                
                # Step 2: Player Check
                # VLC States: 0:Nothing, 3:Playing, 4:Paused, 5:Stopped, 6:Ended, 7:Error
                state = self.player.get_state()
                
                if state == vlc.State.Playing:
                    # Everything is fine, hide overlay
                    self.update_ui_message("", show=False)
                
                elif state in [vlc.State.Error, vlc.State.Ended, vlc.State.Stopped]:
                    # Stream died but network is okay
                    self.update_ui_message("Stream Lost.\nReconnecting...", "yellow")
                    print(f"Stream lost (State: {state}). Restarting...")
                    self.restart_stream()
            
            else:
                # Ping Failed
                self.network_ok = False
                self.player.stop()
                msg = f"NETWORK ERROR\nCannot reach NVR at {self.config['nvr_ip']}"
                self.update_ui_message(msg, "red")
            
            # Wait before next check
            time.sleep(self.config["ping_interval"])

    # ==========================================
    # ADMIN DASHBOARD
    # ==========================================
    def open_admin_panel(self, event=None):
        """Shows password prompt and opens dashboard."""
        pwd = simpledialog.askstring("Admin Access", "Enter Password:", parent=self.root, show='*')
        if pwd == self.config["admin_pass"]:
            self.show_dashboard_window()
        elif pwd is not None:
            messagebox.showerror("Access Denied", "Incorrect Password")

    def show_dashboard_window(self):
        """Displays the Stats & Settings window."""
        dash = Toplevel(self.root)
        dash.title("System Dashboard")
        dash.geometry("500x550")
        dash.configure(bg="#1a1a1a")

        # --- Section: System Stats ---
        Label(dash, text="SYSTEM HEALTH", font=("Arial", 12, "bold"), bg="#1a1a1a", fg="#00ff00").pack(pady=10)
        
        stats_frame = tk.Frame(dash, bg="#333", padx=10, pady=10)
        stats_frame.pack(fill="x", padx=20)
        
        lbl_cpu = Label(stats_frame, text="CPU: ...", bg="#333", fg="white", font=("Monospace", 10))
        lbl_cpu.pack(anchor="w")
        lbl_ram = Label(stats_frame, text="RAM: ...", bg="#333", fg="white", font=("Monospace", 10))
        lbl_ram.pack(anchor="w")
        lbl_temp = Label(stats_frame, text="Temp: ...", bg="#333", fg="white", font=("Monospace", 10))
        lbl_temp.pack(anchor="w")
        lbl_uptime = Label(stats_frame, text="Uptime: ...", bg="#333", fg="white", font=("Monospace", 10))
        lbl_uptime.pack(anchor="w")

        def update_stats():
            if not dash.winfo_exists(): return
            
            # CPU/RAM
            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory().percent
            
            # Temp (Raspberry Pi specific)
            temp = "N/A"
            try:
                with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                    temp = f"{int(f.read()) / 1000:.1f} °C"
            except:
                pass 
            
            # Uptime
            uptime = datetime.now() - datetime.fromtimestamp(psutil.boot_time())
            uptime_str = str(uptime).split('.')[0]

            lbl_cpu.config(text=f"CPU Load  : {cpu}%")
            lbl_ram.config(text=f"RAM Usage : {ram}%")
            lbl_temp.config(text=f"Core Temp : {temp}")
            lbl_uptime.config(text=f"Sys Uptime: {uptime_str}")
            
            dash.after(2000, update_stats)
        
        update_stats()

        # --- Section: NVR Settings ---
        Label(dash, text="NVR CONFIGURATION", font=("Arial", 12, "bold"), bg="#1a1a1a", fg="#00ccff").pack(pady=15)
        
        form_frame = tk.Frame(dash, bg="#1a1a1a")
        form_frame.pack()
        
        entries = {}
        fields = [
            ("NVR IP Address", "nvr_ip"),
            ("RTSP Port", "nvr_port"),
            ("Username", "nvr_user"),
            ("Password", "nvr_pass"),
            ("Channel ID (0=Zero)", "channel")
        ]
        
        for i, (label, key) in enumerate(fields):
            Label(form_frame, text=label, bg="#1a1a1a", fg="white").grid(row=i, column=0, sticky="e", padx=5, pady=5)
            e = Entry(form_frame, width=25)
            e.insert(0, self.config.get(key, ""))
            e.grid(row=i, column=1, padx=5)
            entries[key] = e

        def save_and_reload():
            for key, entry in entries.items():
                self.config[key] = entry.get()
            self.save_config()
            messagebox.showinfo("Saved", "Configuration saved.\nThe application will now restart the stream.")
            dash.destroy()
            self.restart_stream()

        # Buttons
        btn_frame = tk.Frame(dash, bg="#1a1a1a")
        btn_frame.pack(pady=20)
        
        Button(btn_frame, text="Save & Reload", command=save_and_reload, 
               bg="#007700", fg="white", font=("Arial", 10, "bold"), width=15).pack(side="left", padx=5)
        
        Button(btn_frame, text="Exit Application", command=self.on_close, 
               bg="#770000", fg="white", font=("Arial", 10, "bold"), width=15).pack(side="left", padx=5)

    def on_close(self, event=None):
        """Cleanup resources and exit."""
        self.running = False
        self.player.stop()
        self.root.destroy()
        sys.exit(0)

if __name__ == "__main__":
    # Create main window
    root = tk.Tk()
    app = SmartNVRApp(root)
    # Start main event loop
    root.mainloop()
