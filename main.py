#!/usr/bin/env python3
"""
Smart NVR Viewer for Raspberry Pi (Professional Edition)
--------------------------------------------------------
Description:
    A lightweight, hardware-accelerated RTSP stream decoder designed for 
    24/7 monitoring on Raspberry Pi. It bypasses the need for a PC or 
    HDMI extender by decoding streams directly using the GPU.

Features:
    - Aggressive Kiosk Mode (Forces Fullscreen/Topmost to hide OS)
    - Zero-Channel Support (Decodes 1 stream for 32 cameras)
    - Auto-Healing Network (Watchdog for Ping & Stream Health)
    - Visual Countdown Timer for Reconnections
    - Hidden Admin Dashboard for on-the-fly configuration
    - Stream Quality Selector (Main vs Sub stream) to optimize performance

Author: Mohammad Hadi Rezaei
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
# 1. DEPENDENCY CHECK & SAFETY STARTUP
# ==========================================
# We wrap critical imports in a try-block. If dependencies are missing 
# (e.g., user didn't run setup.sh), we show a GUI error instead of crashing 
# to a black console, which is helpful in Kiosk mode.
try:
    import vlc      # LibVLC bindings for hardware-accelerated video decoding
    import psutil   # System monitoring (CPU/RAM/Uptime)
except ImportError as e:
    # Initialize a temporary Tkinter root just to show the error
    root = tk.Tk()
    root.withdraw() # Hide the main window frame
    error_message = (
        f"CRITICAL STARTUP ERROR:\n"
        f"Missing required Python libraries: {e}\n\n"
        f"SOLUTION:\n"
        f"Please run the 'setup.sh' script again to install dependencies.\n"
        f"This app requires a Python Virtual Environment (venv) on RPi Bookworm+."
    )
    messagebox.showerror("Dependency Error", error_message)
    sys.exit(1)

# ==========================================
# 2. CONFIGURATION & DEFAULTS
# ==========================================
CONFIG_FILE = "nvr_config.json"

# Default settings applied if config file is missing
DEFAULT_CONFIG = {
    "nvr_ip": "192.168.1.108",    # Target NVR IP Address
    "nvr_port": "554",            # Standard RTSP Port
    "nvr_user": "admin",          # Default User
    "nvr_pass": "admin123",       # Default Password
    "channel": "0",               # 0 = Zero Channel (Overview), 1+ = Specific Camera
    "subtype": "1",               # 0 = Main Stream (High Res), 1 = Sub Stream (Low Res/Fast)
    "admin_pass": "admin",        # Password to access Ctrl+Alt+S menu
    "ping_interval": 10,          # Seconds to wait between network health checks
    "network_caching": 300        # VLC Buffer in ms (Lower = Less Latency, Higher = Smoother)
}

class SmartNVRApp:
    def __init__(self, root):
        """
        Main Application Class.
        Initializes the GUI, VLC Player, and Background Monitoring Threads.
        """
        self.root = root
        self.root.title("Smart NVR Viewer")
        self.root.configure(bg="black")
        
        # --- Load User Configuration ---
        self.config = self.load_config()
        
        # --- System Level Optimizations ---
        # Disable screen blanking/sleeping to ensure 24/7 visibility
        self.disable_screensaver()
        
        # --- KIOSK MODE SETUP ---
        # 1. Set Fullscreen to cover the entire OS UI
        self.root.attributes('-fullscreen', True)
        # 2. Set Topmost to ensure no system popups/taskbars overlay the video
        self.root.attributes('-topmost', True) 
        # 3. Hide the Mouse Cursor (Clean look)
        self.root.config(cursor="none")
        
        # --- Build UI Layout ---
        self.setup_ui()
        
        # --- VLC Player Initialization ---
        # Arguments explained:
        # --no-xlib: Vital for threading stability on Linux/X11
        # --rtsp-tcp: Forces TCP transport. UDP often causes gray artifacts/packet loss on WiFi/LAN.
        # --network-caching: Controls latency.
        # --clock-jitter=0: Disables jitter compensation for lower latency.
        vlc_args = [
            "--no-xlib", 
            f"--network-caching={self.config.get('network_caching', 300)}",
            "--rtsp-tcp",
            "--clock-jitter=0",
            "--clock-synchro=0",
            "--quiet"
        ]
        self.vlc_instance = vlc.Instance(*vlc_args)
        self.player = self.vlc_instance.media_player_new()
        
        # Embed the player into the Tkinter window
        self.embed_player()

        # --- Runtime State Variables ---
        self.running = True      # Main loop control flag
        self.retry_count = 0     # Tracks failed connection attempts
        self.network_ok = False  # Tracks ping status

        # --- Input Bindings (Shortcuts) ---
        # Ctrl + Alt + S : Opens the Hidden Admin Dashboard
        self.root.bind("<Control-Alt-s>", self.open_admin_panel)
        # ESC : Emergency Exit (Closes app)
        self.root.bind("<Escape>", self.on_close)
        # F5 : Force Refresh (Reloads stream)
        self.root.bind("<F5>", self.restart_stream)

        # --- Start Background Tasks ---
        # 1. Monitor Thread: Handles Ping, Connection Logic, and Watchdog
        self.monitor_thread = threading.Thread(target=self.system_monitor_loop, daemon=True)
        self.monitor_thread.start()

        # 2. UI Periodic Tasks: Clock update and Kiosk enforcement
        self.update_clock()
        self.enforce_kiosk_mode()

    def setup_ui(self):
        """Constructs the visual elements of the application."""
        # Container for the Video
        self.video_frame = tk.Frame(self.root, bg="black")
        self.video_frame.pack(fill=tk.BOTH, expand=True)
        
        # Status Label: Displays large errors or "Initializing" messages
        self.status_label = tk.Label(
            self.root, 
            text="Initializing System...", 
            font=("Arial", 28, "bold"), 
            fg="#00ff00", 
            bg="black", 
            wraplength=900
        )
        self.status_label.place(relx=0.5, rely=0.5, anchor="center")

        # Sub-Status Label: Displays "Retrying in X seconds..."
        self.sub_status_label = tk.Label(
            self.root, 
            text="", 
            font=("Arial", 16), 
            fg="yellow", 
            bg="black"
        )
        self.sub_status_label.place(relx=0.5, rely=0.6, anchor="center")
        
        # Clock: Top-right digital clock to indicate system is not frozen
        self.clock_label = tk.Label(
            self.root, 
            text="00:00:00", 
            font=("Monospace", 14, "bold"), 
            bg="black", 
            fg="#888888"
        )
        self.clock_label.place(relx=0.98, rely=0.02, anchor="ne")

    def load_config(self):
        """Loads settings from JSON. Creates default file if missing."""
        if not os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(DEFAULT_CONFIG, f, indent=4)
            except IOError as e:
                print(f"Error creating config file: {e}")
            return DEFAULT_CONFIG.copy()
        else:
            try:
                with open(CONFIG_FILE, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                print("Config file corrupted. Using defaults.")
                return DEFAULT_CONFIG.copy()

    def save_config(self):
        """Saves current memory settings to JSON file."""
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=4)
        except IOError as e:
            messagebox.showerror("Save Error", f"Could not save settings: {e}")

    def disable_screensaver(self):
        """
        Executes Linux X11 commands to disable power saving and screensavers.
        Essential for keeping the monitor on 24/7.
        """
        if sys.platform.startswith('linux'):
            try:
                subprocess.run(["xset", "s", "off"], check=False)      # Disable screensaver timer
                subprocess.run(["xset", "-dpms"], check=False)         # Disable Energy Star features
                subprocess.run(["xset", "s", "noblank"], check=False)  # Prevent screen blanking
            except FileNotFoundError:
                # 'xset' might not be installed on minimal systems
                pass

    def embed_player(self):
        """
        Binds the VLC player instance to the Tkinter Frame.
        Uses platform-specific Window IDs (XID on Linux, HWND on Windows).
        """
        if sys.platform.startswith('linux'):
            self.player.set_xwindow(self.video_frame.winfo_id())
        elif sys.platform == "win32":
            self.player.set_hwnd(self.video_frame.winfo_id())

    def enforce_kiosk_mode(self):
        """
        Aggressively checks every 5 seconds if the window is still fullscreen 
        and on top. This fixes issues where the Taskbar might appear after a reboot.
        """
        try:
            if not self.root.attributes('-fullscreen'):
                self.root.attributes('-fullscreen', True)
            self.root.attributes('-topmost', True)
        except Exception:
            pass
        # Schedule next check
        self.root.after(5000, self.enforce_kiosk_mode)

    def update_clock(self):
        """Updates the digital clock every second."""
        now = datetime.now().strftime("%H:%M:%S")
        self.clock_label.config(text=now)
        self.root.after(1000, self.update_clock)

    def update_ui_message(self, main_text, sub_text="", color="white", show=True):
        """
        Thread-safe method to update overlay text.
        Called from the background monitor thread.
        """
        def _update():
            if show:
                self.status_label.config(text=main_text, fg=color)
                self.sub_status_label.config(text=sub_text)
                # Bring labels to front (Z-Index) so they appear over the video
                self.status_label.lift()
                self.sub_status_label.lift()
                self.clock_label.lift()
            else:
                # Send labels to back to reveal the video
                self.status_label.lower()
                self.sub_status_label.lower()
        self.root.after(0, _update)

    def build_rtsp_url(self):
        """Constructs the RTSP Connection String based on config."""
        # Format: rtsp://user:pass@ip:port/cam/realmonitor?channel=X&subtype=Y
        return (f"rtsp://{self.config['nvr_user']}:{self.config['nvr_pass']}@"
                f"{self.config['nvr_ip']}:{self.config['nvr_port']}/"
                f"cam/realmonitor?channel={self.config['channel']}&subtype={self.config['subtype']}")

    def check_ping(self):
        """
        Pings the NVR to verify physical network connectivity.
        Returns: True if NVR is reachable, False otherwise.
        """
        ip = self.config["nvr_ip"]
        try:
            param = '-n' if sys.platform.lower()=='win32' else '-c'
            # Timeout (-W) set to 2 seconds to prevent locking the thread too long
            output = subprocess.run(
                ["ping", param, "1", "-W", "2", ip], 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL
            )
            return output.returncode == 0
        except Exception:
            return False

    def start_stream(self):
        """Creates a new VLC media instance and starts playback."""
        url = self.build_rtsp_url()
        # Log obscured URL for debugging security
        safe_url = url.replace(self.config['nvr_pass'], "****")
        print(f"Connecting to RTSP Stream: {safe_url}")
        
        media = self.vlc_instance.media_new(url)
        self.player.set_media(media)
        self.player.play()

    def restart_stream(self, event=None):
        """
        Stops and restarts the stream. 
        Can be triggered by F5 key event or internal logic.
        """
        print("Restarting Stream...")
        self.player.stop()
        time.sleep(0.5) # Brief pause to allow VLC to release resources
        self.start_stream()

    def system_monitor_loop(self):
        """
        The Core Watchdog Loop. Runs in a separate thread.
        Logic:
        1. Ping NVR.
        2. If Ping fails -> Show Network Error -> Wait.
        3. If Ping OK -> Check VLC Player State.
        4. If Player Stopped/Error -> Show Stream Error -> Retry.
        5. If Player Playing -> Hide Errors -> Success.
        """
        while self.running:
            # Step 1: Network Connectivity Check
            is_reachable = self.check_ping()

            if is_reachable:
                # Case: Network just recovered
                if not self.network_ok:
                    self.retry_count = 0
                    self.network_ok = True
                    self.update_ui_message("Network Found!", "Establishing Video Feed...", "#00ff00")
                    self.restart_stream()
                    time.sleep(3) # Wait for stream buffer

                # Step 2: Stream Status Check
                state = self.player.get_state()
                
                if state == vlc.State.Playing:
                    # Success State
                    self.update_ui_message("", show=False)
                    self.retry_count = 0 
                
                elif state in [vlc.State.Error, vlc.State.Ended, vlc.State.Stopped]:
                    # Failure State (Auth error, Bad Channel, or Stream ended)
                    self.retry_count += 1
                    self.update_ui_message(
                        "Stream Connection Failed", 
                        f"Check Settings (Attempt #{self.retry_count})", 
                        "orange"
                    )
                    # Visual Countdown before retrying
                    self.perform_countdown(5)
                    self.restart_stream()
            else:
                # Failure Case: Network Down
                self.network_ok = False
                self.player.stop()
                self.retry_count += 1
                
                error_msg = f"NETWORK ERROR\nCannot Reach NVR: {self.config['nvr_ip']}"
                wait_time = self.config.get("ping_interval", 10)
                
                self.perform_countdown(wait_time, error_msg, "red")

    def perform_countdown(self, seconds, main_text="Retrying...", color="white"):
        """
        Helper to display a countdown timer on the UI.
        Blocks the monitor thread (not GUI) for 'seconds' duration.
        """
        for i in range(seconds, 0, -1):
            if not self.running: break
            sub_text = f"Retrying in {i} seconds... (Attempt #{self.retry_count})"
            self.update_ui_message(main_text, sub_text, color)
            time.sleep(1)

    # ==========================================
    # 3. ADMIN DASHBOARD & SETTINGS
    # ==========================================
    def open_admin_panel(self, event=None):
        """
        Triggered by Ctrl+Alt+S.
        Shows password prompt and opens the Dashboard.
        """
        # Enable mouse cursor so user can click
        self.root.config(cursor="arrow")
        
        pwd = simpledialog.askstring("Admin Access", "Enter Password:", parent=self.root, show='*')
        
        if pwd == self.config["admin_pass"]:
            self.show_dashboard_window()
        else:
            if pwd is not None: 
                messagebox.showerror("Access Denied", "Incorrect Password")
            # Hide cursor again if failed/cancelled
            self.root.config(cursor="none")

    def show_dashboard_window(self):
        """Creates the Admin Dashboard Toplevel Window."""
        dash = Toplevel(self.root)
        dash.title("System Dashboard")
        dash.geometry("500x700")
        dash.configure(bg="#1a1a1a")
        
        # Ensure dashboard is always on top of the fullscreen video
        dash.attributes('-topmost', True)

        def on_dash_close():
            """Clean up when dashboard closes."""
            self.root.config(cursor="none") # Hide cursor again
            dash.destroy()
        
        dash.protocol("WM_DELETE_WINDOW", on_dash_close)
        
        # --- System Statistics Section ---
        Label(dash, text="SYSTEM STATS", font=("Arial", 12, "bold"), bg="#1a1a1a", fg="#00ff00").pack(pady=10)
        
        stats_frame = tk.Frame(dash, bg="#333", padx=10, pady=10)
        stats_frame.pack(fill="x", padx=20)
        
        l_cpu = Label(stats_frame, text="CPU: ...", bg="#333", fg="white"); l_cpu.pack(anchor="w")
        l_ram = Label(stats_frame, text="RAM: ...", bg="#333", fg="white"); l_ram.pack(anchor="w")
        
        # Inner function to update stats dynamically
        def update_stats():
            if not dash.winfo_exists(): return
            try:
                l_cpu.config(text=f"CPU Load: {psutil.cpu_percent()}%")
                l_ram.config(text=f"RAM Usage: {psutil.virtual_memory().percent}%")
            except Exception: pass
            dash.after(2000, update_stats)
        update_stats()

        # --- NVR Settings Section ---
        Label(dash, text="NVR CONFIGURATION", font=("Arial", 12, "bold"), bg="#1a1a1a", fg="#00ccff").pack(pady=10)
        f_frame = tk.Frame(dash, bg="#1a1a1a"); f_frame.pack()
        
        entries = {}
        # Configuration Fields
        fields = [
            ("NVR IP Address", "nvr_ip"), 
            ("Username", "nvr_user"), 
            ("Password", "nvr_pass"), 
            ("Channel ID (0=Zero)", "channel"),
            ("Quality (0=Main, 1=Sub)", "subtype") # Subtype 1 fixes lag on RPi
        ]
        
        # Generate Input Fields
        for i, (label_text, key) in enumerate(fields):
            Label(f_frame, text=label_text, bg="#1a1a1a", fg="white").grid(row=i, column=0, sticky="e", padx=5, pady=5)
            e = Entry(f_frame)
            e.insert(0, self.config.get(key, ""))
            e.grid(row=i, column=1)
            entries[key] = e
            
        def save_changes():
            """Saves inputs to config and restarts stream."""
            for key, entry in entries.items():
                self.config[key] = entry.get()
            self.save_config()
            messagebox.showinfo("Success", "Settings Saved!\nRestarting Stream now...")
            on_dash_close()
            self.restart_stream()
            
        Button(dash, text="SAVE SETTINGS", command=save_changes, bg="green", fg="white", width=20, font=("Arial", 10, "bold")).pack(pady=15)
        
        # Divider Line
        tk.Frame(dash, height=1, bg="#555").pack(fill="x", padx=20, pady=10)

        # --- App Control Section ---
        Button(dash, text="EXIT APPLICATION", command=self.on_close, bg="#cc0000", fg="white", font=("Arial", 10, "bold"), width=20).pack(pady=5)
        Label(dash, text="(Closes app & returns to desktop)", font=("Arial", 8), bg="#1a1a1a", fg="#888").pack()

    def on_close(self, event=None):
        """Safely shuts down the application."""
        self.running = False
        self.player.stop()
        self.root.destroy()
        sys.exit(0)

if __name__ == "__main__":
    # Initialize Main Tkinter Loop
    root = tk.Tk()
    app = SmartNVRApp(root)
    root.mainloop()
