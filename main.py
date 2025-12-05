#!/usr/bin/env python3
"""
Smart NVR Viewer for Raspberry Pi (Professional Edition)
--------------------------------------------------------
Features:
- Aggressive Kiosk Mode (Forces Fullscreen/Topmost)
- Visual Countdown Timer for Reconnection
- Retry Counter
- On-screen Clock
- Zero Channel Optimization
- F5 Refresh Support

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
# DEPENDENCY CHECK
# ==========================================
try:
    import vlc
    import psutil
except ImportError as e:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Startup Error", f"Missing Libraries:\n{e}\nPlease run setup.sh again.")
    sys.exit(1)

# ==========================================
# CONFIGURATION
# ==========================================
CONFIG_FILE = "nvr_config.json"
DEFAULT_CONFIG = {
    "nvr_ip": "192.168.1.108",
    "nvr_port": "554",
    "nvr_user": "admin",
    "nvr_pass": "admin123",
    "channel": "0",
    "subtype": "0",
    "admin_pass": "admin",
    "ping_interval": 10,
    "network_caching": 600
}

class SmartNVRApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Smart NVR Viewer")
        self.root.configure(bg="black")
        
        # Load Config
        self.config = self.load_config()
        
        # --- System Optimization ---
        self.disable_screensaver()
        
        # --- KIOSK MODE SETUP ---
        # 1. Fullscreen
        self.root.attributes('-fullscreen', True)
        # 2. Topmost (Keeps window above taskbar)
        self.root.attributes('-topmost', True) 
        # 3. Hide Cursor
        self.root.config(cursor="none")
        
        # UI Structure
        self.setup_ui()
        
        # VLC Setup
        vlc_args = [
            "--no-xlib", 
            f"--network-caching={self.config.get('network_caching', 600)}",
            "--rtsp-tcp", # Force TCP for stability
            "--quiet"
        ]
        self.vlc_instance = vlc.Instance(*vlc_args)
        self.player = self.vlc_instance.media_player_new()
        self.embed_player()

        # Variables
        self.running = True
        self.retry_count = 0
        self.network_ok = False

        # Bindings
        self.root.bind("<Control-Alt-s>", self.open_admin_panel)
        self.root.bind("<Escape>", self.on_close)
        self.root.bind("<F5>", self.restart_stream) # F5 for Refresh

        # Start Background Threads
        self.monitor_thread = threading.Thread(target=self.system_monitor_loop, daemon=True)
        self.monitor_thread.start()

        # Start UI periodic tasks (Clock & Kiosk Enforcer)
        self.update_clock()
        self.enforce_kiosk_mode()

    def setup_ui(self):
        """Builds the user interface elements."""
        # Video Frame
        self.video_frame = tk.Frame(self.root, bg="black")
        self.video_frame.pack(fill=tk.BOTH, expand=True)
        
        # Central Status Overlay (Big Text)
        self.status_label = tk.Label(
            self.root, 
            text="Initializing System...", 
            font=("Arial", 28, "bold"), 
            fg="#00ff00", 
            bg="black",
            wraplength=900,
            justify="center"
        )
        self.status_label.place(relx=0.5, rely=0.5, anchor="center")

        # Retry Counter (Smaller text below status)
        self.sub_status_label = tk.Label(
            self.root,
            text="",
            font=("Arial", 16),
            fg="yellow",
            bg="black"
        )
        self.sub_status_label.place(relx=0.5, rely=0.6, anchor="center")
        
        # Digital Clock (Top Right)
        self.clock_label = tk.Label(
            self.root, text="00:00:00", font=("Monospace", 14, "bold"),
            bg="black", fg="#888888"
        )
        self.clock_label.place(relx=0.98, rely=0.02, anchor="ne")

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'w') as f:
                json.dump(DEFAULT_CONFIG, f, indent=4)
            return DEFAULT_CONFIG.copy()
        else:
            try:
                with open(CONFIG_FILE, 'r') as f:
                    return json.load(f)
            except:
                return DEFAULT_CONFIG.copy()

    def save_config(self):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=4)

    def disable_screensaver(self):
        if sys.platform.startswith('linux'):
            try:
                subprocess.run(["xset", "s", "off"])
                subprocess.run(["xset", "-dpms"])
                subprocess.run(["xset", "s", "noblank"])
            except:
                pass

    def embed_player(self):
        if sys.platform.startswith('linux'):
            self.player.set_xwindow(self.video_frame.winfo_id())
        elif sys.platform == "win32":
            self.player.set_hwnd(self.video_frame.winfo_id())

    def enforce_kiosk_mode(self):
        """
        Aggressively checks if window is fullscreen and on top.
        Runs every 5 seconds.
        """
        try:
            # Re-apply fullscreen if lost
            if not self.root.attributes('-fullscreen'):
                self.root.attributes('-fullscreen', True)
            
            # Re-apply topmost to cover taskbar
            self.root.attributes('-topmost', True)
        except Exception:
            pass
        
        self.root.after(5000, self.enforce_kiosk_mode)

    def update_clock(self):
        """Updates the top-right clock."""
        now = datetime.now().strftime("%H:%M:%S")
        self.clock_label.config(text=now)
        self.root.after(1000, self.update_clock)

    def update_ui_message(self, main_text, sub_text="", color="white", show=True):
        """Updates the overlay messages safely from threads."""
        def _update():
            if show:
                self.status_label.config(text=main_text, fg=color)
                self.sub_status_label.config(text=sub_text)
                
                # Bring labels to front (above video)
                self.status_label.lift()
                self.sub_status_label.lift()
                self.clock_label.lift()
            else:
                self.status_label.lower()
                self.sub_status_label.lower()
        self.root.after(0, _update)

    def build_rtsp_url(self):
        return (f"rtsp://{self.config['nvr_user']}:{self.config['nvr_pass']}@"
                f"{self.config['nvr_ip']}:{self.config['nvr_port']}/"
                f"cam/realmonitor?channel={self.config['channel']}&subtype={self.config['subtype']}")

    def check_ping(self):
        ip = self.config["nvr_ip"]
        try:
            param = '-n' if sys.platform.lower()=='win32' else '-c'
            # Timeout set to 2 seconds
            output = subprocess.run(["ping", param, "1", "-W", "2", ip], 
                                  stdout=subprocess.DEVNULL, 
                                  stderr=subprocess.DEVNULL)
            return output.returncode == 0
        except:
            return False

    def start_stream(self):
        url = self.build_rtsp_url()
        # Log obscured URL for safety
        safe_url = url.replace(self.config['nvr_pass'], "****")
        print(f"Connecting to: {safe_url}")
        
        media = self.vlc_instance.media_new(url)
        self.player.set_media(media)
        self.player.play()

    def restart_stream(self, event=None):
        """Stops and restarts the stream. Accepts event for key binding."""
        self.player.stop()
        # Small delay to ensure VLC cleans up
        time.sleep(0.5)
        self.start_stream()

    def system_monitor_loop(self):
        """
        Main Logic Loop:
        - Handles Ping
        - Handles Retry Countdown
        - Handles Stream Monitoring
        """
        while self.running:
            # 1. Check Network
            is_reachable = self.check_ping()

            if is_reachable:
                # If we were previously offline, reset counter
                if not self.network_ok:
                    self.retry_count = 0
                    self.network_ok = True
                    self.update_ui_message("Network Found!", "Establishing Video Feed...", "#00ff00")
                    self.restart_stream()
                    time.sleep(3) # Wait for buffer

                # Check Player State
                state = self.player.get_state()
                
                if state == vlc.State.Playing:
                    # All Good
                    self.update_ui_message("", show=False)
                    self.retry_count = 0 # Reset counter on success
                
                elif state in [vlc.State.Error, vlc.State.Ended, vlc.State.Stopped]:
                    # Network is OK, but Video Failed (Auth error? Bad URL?)
                    self.retry_count += 1
                    self.update_ui_message(
                        "Stream Connection Failed", 
                        f"Check Username/Password/Channel Settings\n(Attempt #{self.retry_count})", 
                        "orange"
                    )
                    # Use a short countdown even for stream errors
                    self.perform_countdown(5)
                    self.restart_stream()

            else:
                # Network is DOWN
                self.network_ok = False
                self.player.stop()
                self.retry_count += 1
                
                # Show Error with Counter
                main_msg = f"NETWORK ERROR\nCannot Reach NVR: {self.config['nvr_ip']}"
                
                # Start Countdown Visualization
                wait_time = self.config.get("ping_interval", 10)
                self.perform_countdown(wait_time, main_msg, "red")

    def perform_countdown(self, seconds, main_text="Retrying...", color="white"):
        """Blocks the thread but updates UI with a countdown."""
        for i in range(seconds, 0, -1):
            if not self.running: break
            sub_text = f"Retrying in {i} seconds... (Attempt #{self.retry_count})"
            self.update_ui_message(main_text, sub_text, color)
            time.sleep(1)

    # ==========================================
    # ADMIN PANEL
    # ==========================================
    def open_admin_panel(self, event=None):
        # Allow mouse cursor when admin panel is open
        self.root.config(cursor="arrow")
        pwd = simpledialog.askstring("Admin Access", "Enter Password:", parent=self.root, show='*')
        if pwd == self.config["admin_pass"]:
            self.show_dashboard_window()
        else:
            if pwd is not None: messagebox.showerror("Error", "Wrong Password")
            # Hide cursor again if failed/cancelled
            self.root.config(cursor="none")

    def show_dashboard_window(self):
        dash = Toplevel(self.root)
        dash.title("Dashboard")
        dash.geometry("500x650") # Increased height for Exit button
        dash.configure(bg="#1a1a1a")
        
        # Ensure dashboard is on top of the fullscreen window
        dash.attributes('-topmost', True)

        def on_dash_close():
            self.root.config(cursor="none") # Hide cursor again
            dash.destroy()
        
        dash.protocol("WM_DELETE_WINDOW", on_dash_close)
        
        # --- Stats Section ---
        Label(dash, text="SYSTEM STATS", font=("Arial", 12, "bold"), bg="#1a1a1a", fg="#00ff00").pack(pady=10)
        
        stats_frame = tk.Frame(dash, bg="#333", padx=10, pady=10)
        stats_frame.pack(fill="x", padx=20)
        
        l_cpu = Label(stats_frame, text="CPU: ...", bg="#333", fg="white"); l_cpu.pack(anchor="w")
        l_ram = Label(stats_frame, text="RAM: ...", bg="#333", fg="white"); l_ram.pack(anchor="w")
        l_tmp = Label(stats_frame, text="TMP: ...", bg="#333", fg="white"); l_tmp.pack(anchor="w")
        l_upt = Label(stats_frame, text="UPT: ...", bg="#333", fg="white"); l_upt.pack(anchor="w")

        def update_stats():
            if not dash.winfo_exists(): return
            try:
                cpu = psutil.cpu_percent()
                ram = psutil.virtual_memory().percent
                temp = "N/A"
                if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
                    with open("/sys/class/thermal/thermal_zone0/temp") as f:
                        temp = f"{int(f.read())/1000:.1f}C"
                upt = str(datetime.now() - datetime.fromtimestamp(psutil.boot_time())).split('.')[0]
                
                l_cpu.config(text=f"CPU: {cpu}%")
                l_ram.config(text=f"RAM: {ram}%")
                l_tmp.config(text=f"TEMP: {temp}")
                l_upt.config(text=f"UPTIME: {upt}")
            except: pass
            dash.after(2000, update_stats)
        update_stats()

        # --- Settings Section ---
        Label(dash, text="SETTINGS", font=("Arial", 12, "bold"), bg="#1a1a1a", fg="#00ccff").pack(pady=10)
        f_frame = tk.Frame(dash, bg="#1a1a1a"); f_frame.pack()
        
        entries = {}
        fields = [("IP", "nvr_ip"), ("User", "nvr_user"), ("Pass", "nvr_pass"), ("Ch(0=Zero)", "channel")]
        for i, (txt, key) in enumerate(fields):
            Label(f_frame, text=txt, bg="#1a1a1a", fg="white").grid(row=i, column=0, sticky="e", padx=5, pady=5)
            e = Entry(f_frame); e.insert(0, self.config.get(key, ""))
            e.grid(row=i, column=1)
            entries[key] = e
            
        def save():
            for k, e in entries.items(): self.config[k] = e.get()
            self.save_config()
            messagebox.showinfo("Saved", "Restarting Stream...")
            on_dash_close()
            self.restart_stream()
            
        Button(dash, text="SAVE SETTINGS", command=save, bg="green", fg="white", width=20).pack(pady=15)
        
        # Divider
        tk.Frame(dash, height=1, bg="#555").pack(fill="x", padx=20, pady=10)

        # --- Exit Section ---
        Button(dash, text="EXIT APPLICATION", command=self.on_close, bg="#cc0000", fg="white", font=("Arial", 10, "bold"), width=20).pack(pady=5)
        Label(dash, text="(Closes app & returns to desktop)", font=("Arial", 8), bg="#1a1a1a", fg="#888").pack()

    def on_close(self, event=None):
        self.running = False
        self.player.stop()
        self.root.destroy()
        sys.exit(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = SmartNVRApp(root)
    root.mainloop()
