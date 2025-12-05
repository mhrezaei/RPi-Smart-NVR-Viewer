#!/usr/bin/env python3
"""
Smart NVR Viewer for Raspberry Pi (Professional Master Edition)
---------------------------------------------------------------
Description:
    A robust, hardware-accelerated RTSP stream decoder designed for 
    24/7 monitoring on Raspberry Pi. It creates a dedicated monitoring 
    station without requiring a PC.

Features:
    - Aggressive Kiosk Mode (Fullscreen/Topmost/No Cursor)
    - Zero-Channel Support (Decodes 1 stream for 32 cameras)
    - Auto-Healing Network (Watchdog for Ping & Stream Health)
    - Visual Countdown Timer for Reconnections
    - Advanced Admin Dashboard:
        * Real-time CPU & RAM Load
        * CPU Temperature (Thermal Throttling check)
        * Disk Usage (SD Card health)
        * Network Usage (Total Data Received)
        * Real-time Network Latency (Ping in ms)
    - Stream Quality Selector (Main vs Sub stream)

Author: Mohammad Hadi Rezaei
License: MIT
"""

import sys
import os
import time
import json
import threading
import subprocess
import re  # Required for parsing Ping latency
import tkinter as tk
from tkinter import simpledialog, messagebox, Toplevel, Label, Entry, Button
from datetime import datetime

# ==========================================
# 1. DEPENDENCY CHECK & SAFETY STARTUP
# ==========================================
# Ensures all required libraries are installed before launching the GUI.
try:
    import vlc      # LibVLC bindings for hardware decoding
    import psutil   # Advanced system monitoring
except ImportError as e:
    root = tk.Tk()
    root.withdraw()
    error_message = (
        f"CRITICAL STARTUP ERROR:\n"
        f"Missing required Python libraries: {e}\n\n"
        f"SOLUTION:\n"
        f"Please run the 'setup.sh' script again.\n"
        f"This app requires a Python Virtual Environment (venv)."
    )
    messagebox.showerror("Dependency Error", error_message)
    sys.exit(1)

# ==========================================
# 2. CONFIGURATION & DEFAULTS
# ==========================================
CONFIG_FILE = "nvr_config.json"
DEFAULT_CONFIG = {
    "nvr_ip": "192.168.1.108",    # Target NVR IP
    "nvr_port": "554",            # RTSP Port
    "nvr_user": "admin",          # Username
    "nvr_pass": "admin123",       # Password
    "channel": "0",               # 0 = Zero Channel, 1+ = Camera ID
    "subtype": "1",               # 0 = Main Stream, 1 = Sub Stream (Recommended for Pi)
    "admin_pass": "admin",        # Dashboard Password
    "ping_interval": 10,          # Watchdog check interval
    "network_caching": 300        # Buffer in ms (Lower = Less Latency)
}

class SmartNVRApp:
    def __init__(self, root):
        """
        Main Application Class.
        Initializes GUI, Player, and Background Services.
        """
        self.root = root
        self.root.title("Smart NVR Viewer")
        self.root.configure(bg="black")
        
        # Load Config
        self.config = self.load_config()
        
        # --- System Optimizations ---
        self.disable_screensaver()
        
        # --- Kiosk Mode Setup ---
        self.root.attributes('-fullscreen', True)
        self.root.attributes('-topmost', True) 
        self.root.config(cursor="none")
        
        # --- UI Layout ---
        self.setup_ui()
        
        # --- VLC Initialization ---
        # Optimized arguments for Raspberry Pi hardware decoding
        vlc_args = [
            "--no-xlib", 
            f"--network-caching={self.config.get('network_caching', 300)}",
            "--rtsp-tcp",       # Force TCP to prevent gray artifacts
            "--clock-jitter=0", # Reduce latency
            "--clock-synchro=0",
            "--quiet"
        ]
        self.vlc_instance = vlc.Instance(*vlc_args)
        self.player = self.vlc_instance.media_player_new()
        self.embed_player()

        # --- State Variables ---
        self.running = True
        self.retry_count = 0
        self.network_ok = False

        # --- Key Bindings ---
        self.root.bind("<Control-Alt-s>", self.open_admin_panel)
        self.root.bind("<Escape>", self.on_close)
        self.root.bind("<F5>", self.restart_stream)

        # --- Background Threads ---
        # Starts the main watchdog loop
        self.monitor_thread = threading.Thread(target=self.system_monitor_loop, daemon=True)
        self.monitor_thread.start()

        # --- UI Periodic Tasks ---
        self.update_clock()
        self.enforce_kiosk_mode()

    def setup_ui(self):
        """Builds the visual interface."""
        self.video_frame = tk.Frame(self.root, bg="black")
        self.video_frame.pack(fill=tk.BOTH, expand=True)
        
        # Main Status Message
        self.status_label = tk.Label(
            self.root, 
            text="Initializing System...", 
            font=("Arial", 28, "bold"), 
            fg="#00ff00", 
            bg="black", 
            wraplength=900
        )
        self.status_label.place(relx=0.5, rely=0.5, anchor="center")

        # Sub-status (Countdown)
        self.sub_status_label = tk.Label(
            self.root, 
            text="", 
            font=("Arial", 16), 
            fg="yellow", 
            bg="black"
        )
        self.sub_status_label.place(relx=0.5, rely=0.6, anchor="center")
        
        # Digital Clock
        self.clock_label = tk.Label(
            self.root, 
            text="00:00:00", 
            font=("Monospace", 14, "bold"), 
            bg="black", 
            fg="#888888"
        )
        self.clock_label.place(relx=0.98, rely=0.02, anchor="ne")

    def load_config(self):
        """Loads configuration or creates default."""
        if not os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(DEFAULT_CONFIG, f, indent=4)
            except: pass
            return DEFAULT_CONFIG.copy()
        else:
            try:
                with open(CONFIG_FILE, 'r') as f:
                    return json.load(f)
            except: return DEFAULT_CONFIG.copy()

    def save_config(self):
        """Saves current settings to disk."""
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=4)
        except IOError as e:
            messagebox.showerror("Save Error", f"{e}")

    def disable_screensaver(self):
        """Prevents screen from sleeping on Linux."""
        if sys.platform.startswith('linux'):
            try:
                subprocess.run(["xset", "s", "off"], check=False)
                subprocess.run(["xset", "-dpms"], check=False)
                subprocess.run(["xset", "s", "noblank"], check=False)
            except: pass

    def embed_player(self):
        """Connects VLC to Tkinter Window."""
        if sys.platform.startswith('linux'):
            self.player.set_xwindow(self.video_frame.winfo_id())
        elif sys.platform == "win32":
            self.player.set_hwnd(self.video_frame.winfo_id())

    def enforce_kiosk_mode(self):
        """Aggressively keeps window fullscreen and on top."""
        try:
            if not self.root.attributes('-fullscreen'):
                self.root.attributes('-fullscreen', True)
            self.root.attributes('-topmost', True)
        except: pass
        self.root.after(5000, self.enforce_kiosk_mode)

    def update_clock(self):
        """Updates the clock every second."""
        now = datetime.now().strftime("%H:%M:%S")
        self.clock_label.config(text=now)
        self.root.after(1000, self.update_clock)

    def update_ui_message(self, main_text, sub_text="", color="white", show=True):
        """Updates overlay messages safely from threads."""
        def _update():
            if show:
                self.status_label.config(text=main_text, fg=color)
                self.sub_status_label.config(text=sub_text)
                self.status_label.lift()
                self.sub_status_label.lift()
                self.clock_label.lift()
            else:
                self.status_label.lower()
                self.sub_status_label.lower()
        self.root.after(0, _update)

    def build_rtsp_url(self):
        """Generates the connection string."""
        return (f"rtsp://{self.config['nvr_user']}:{self.config['nvr_pass']}@"
                f"{self.config['nvr_ip']}:{self.config['nvr_port']}/"
                f"cam/realmonitor?channel={self.config['channel']}&subtype={self.config['subtype']}")

    def check_ping(self):
        """Simple connectivity check."""
        ip = self.config["nvr_ip"]
        try:
            param = '-n' if sys.platform.lower()=='win32' else '-c'
            output = subprocess.run(["ping", param, "1", "-W", "2", ip], 
                                  stdout=subprocess.DEVNULL, 
                                  stderr=subprocess.DEVNULL)
            return output.returncode == 0
        except: return False

    def get_network_latency(self):
        """
        ADVANCED: Pings the NVR and parses the output to get exact latency in ms.
        Returns: String like "2.45 ms" or "Timeout".
        """
        ip = self.config["nvr_ip"]
        try:
            param = '-n' if sys.platform.lower()=='win32' else '-c'
            result = subprocess.run(
                ["ping", param, "1", "-W", "1", ip], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=True
            )
            if result.returncode == 0:
                # Regex to extract time (e.g., time=2.45 ms)
                match = re.search(r'time[=<]([\d\.]+)', result.stdout)
                if match:
                    return f"{match.group(1)} ms"
                return "<1 ms"
            return "Unreachable"
        except:
            return "Error"

    def start_stream(self):
        """Starts VLC playback."""
        url = self.build_rtsp_url()
        safe_url = url.replace(self.config['nvr_pass'], "****")
        print(f"Connecting to: {safe_url}")
        media = self.vlc_instance.media_new(url)
        self.player.set_media(media)
        self.player.play()

    def restart_stream(self, event=None):
        """Restarts the stream (e.g. on F5 or Config Save)."""
        print("Restarting Stream...")
        self.player.stop()
        time.sleep(0.5)
        self.start_stream()

    def system_monitor_loop(self):
        """
        CORE WATCHDOG: Handles Network, Stream Health, and Reconnection Logic.
        """
        while self.running:
            is_reachable = self.check_ping()

            if is_reachable:
                if not self.network_ok:
                    self.retry_count = 0
                    self.network_ok = True
                    self.update_ui_message("Network Found!", "Establishing Feed...", "#00ff00")
                    self.restart_stream()
                    time.sleep(3)

                state = self.player.get_state()
                if state == vlc.State.Playing:
                    self.update_ui_message("", show=False)
                    self.retry_count = 0 
                elif state in [vlc.State.Error, vlc.State.Ended, vlc.State.Stopped]:
                    self.retry_count += 1
                    self.update_ui_message("Stream Failed", f"Check Settings (Attempt #{self.retry_count})", "orange")
                    self.perform_countdown(5)
                    self.restart_stream()
            else:
                self.network_ok = False
                self.player.stop()
                self.retry_count += 1
                self.perform_countdown(10, f"NETWORK ERROR\nUnreachable: {self.config['nvr_ip']}", "red")

    def perform_countdown(self, seconds, main_text, color="white"):
        """Visual countdown blocker."""
        for i in range(seconds, 0, -1):
            if not self.running: break
            self.update_ui_message(main_text, f"Retrying in {i}s... (Attempt #{self.retry_count})", color)
            time.sleep(1)

    # ==========================================
    # 3. ADVANCED ADMIN DASHBOARD
    # ==========================================
    def open_admin_panel(self, event=None):
        """Opens Dashboard with Password Protection."""
        self.root.config(cursor="arrow")
        pwd = simpledialog.askstring("Admin Access", "Enter Password:", parent=self.root, show='*')
        if pwd == self.config["admin_pass"]:
            self.show_dashboard_window()
        else:
            if pwd is not None: messagebox.showerror("Access Denied", "Incorrect Password")
            self.root.config(cursor="none")

    def show_dashboard_window(self):
        """Displays the Stats & Settings Overlay."""
        dash = Toplevel(self.root)
        dash.title("System Dashboard")
        dash.geometry("500x780")
        dash.configure(bg="#1a1a1a")
        dash.attributes('-topmost', True)

        def on_dash_close():
            self.root.config(cursor="none")
            dash.destroy()
        
        dash.protocol("WM_DELETE_WINDOW", on_dash_close)
        
        # --- Advanced Statistics Section ---
        Label(dash, text="ADVANCED SYSTEM STATS", font=("Arial", 12, "bold"), bg="#1a1a1a", fg="#00ff00").pack(pady=10)
        
        stats_frame = tk.Frame(dash, bg="#333", padx=10, pady=10)
        stats_frame.pack(fill="x", padx=20)
        
        # Placeholders for Stats
        labels = {}
        # Added keys for Disk, Net, Ping
        for key in ["cpu", "ram", "temp", "disk", "net", "ping", "upt"]:
            l = Label(stats_frame, text=f"{key.upper()}: Initializing...", bg="#333", fg="white", font=("Monospace", 10))
            l.pack(anchor="w")
            labels[key] = l

        def get_human_readable_size(size_bytes):
            """Helper to convert bytes to KB/MB/GB."""
            if size_bytes == 0: return "0 B"
            units = ("B", "KB", "MB", "GB", "TB")
            i = 0
            while size_bytes >= 1024 and i < len(units)-1:
                size_bytes /= 1024.0
                i += 1
            return f"{size_bytes:.2f} {units[i]}"

        def update_stats():
            """Loops to update dashboard stats live."""
            if not dash.winfo_exists(): return
            try:
                # 1. CPU & RAM
                labels['cpu'].config(text=f"CPU Load  : {psutil.cpu_percent()}%")
                labels['ram'].config(text=f"RAM Usage : {psutil.virtual_memory().percent}%")
                
                # 2. Temperature
                temp = "N/A"
                if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
                    with open("/sys/class/thermal/thermal_zone0/temp") as f:
                        temp = f"{int(f.read())/1000:.1f} °C"
                labels['temp'].config(text=f"Core Temp : {temp}")
                
                # 3. Disk Usage (SD Card)
                disk = psutil.disk_usage('/')
                labels['disk'].config(text=f"Disk Usage: {disk.percent}% (Free: {get_human_readable_size(disk.free)})")
                
                # 4. Network Usage (Total Downloaded Video Data)
                net = psutil.net_io_counters()
                labels['net'].config(text=f"Net Recv  : {get_human_readable_size(net.bytes_recv)}")

                # 5. Network Latency (Real-time Ping)
                latency = self.get_network_latency()
                # Color code latency: Green < 100ms, Yellow/Red otherwise
                color = "#00ff00" if "ms" in latency and float(latency.split()[0]) < 100 else "orange"
                labels['ping'].config(text=f"NVR Ping  : {latency}", fg=color)

                # 6. Uptime
                upt = str(datetime.now() - datetime.fromtimestamp(psutil.boot_time())).split('.')[0]
                labels['upt'].config(text=f"Sys Uptime: {upt}")

            except Exception as e:
                print(f"Stats Error: {e}")
            
            dash.after(2000, update_stats) # Refresh every 2 seconds
        
        update_stats()

        # --- Settings Section ---
        Label(dash, text="CONFIGURATION", font=("Arial", 12, "bold"), bg="#1a1a1a", fg="#00ccff").pack(pady=10)
        f_frame = tk.Frame(dash, bg="#1a1a1a"); f_frame.pack()
        
        entries = {}
        fields = [
            ("NVR IP", "nvr_ip"), 
            ("Username", "nvr_user"), 
            ("Password", "nvr_pass"), 
            ("Ch (0=Zero)", "channel"),
            ("Quality (0=Main, 1=Sub)", "subtype")
        ]
        
        for i, (txt, key) in enumerate(fields):
            Label(f_frame, text=txt, bg="#1a1a1a", fg="white").grid(row=i, column=0, sticky="e", padx=5, pady=5)
            e = Entry(f_frame)
            e.insert(0, self.config.get(key, ""))
            e.grid(row=i, column=1)
            entries[key] = e
            
        def save():
            for k, e in entries.items(): self.config[k] = e.get()
            self.save_config()
            messagebox.showinfo("Saved", "Settings Saved!\nRestarting Stream...")
            on_dash_close()
            self.restart_stream()
            
        Button(dash, text="SAVE SETTINGS", command=save, bg="green", fg="white", width=20).pack(pady=15)
        
        # Divider
        tk.Frame(dash, height=1, bg="#555").pack(fill="x", padx=20, pady=10)

        # --- Exit Section ---
        Button(dash, text="EXIT APPLICATION", command=self.on_close, bg="#cc0000", fg="white", font=("Arial", 10, "bold"), width=20).pack(pady=5)
        Label(dash, text="(Closes app & returns to desktop)", font=("Arial", 8), bg="#1a1a1a", fg="#888").pack()

    def on_close(self, event=None):
        """Clean shutdown."""
        self.running = False
        self.player.stop()
        self.root.destroy()
        sys.exit(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = SmartNVRApp(root)
    root.mainloop()
