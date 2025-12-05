#!/usr/bin/env python3
"""
Smart NVR Viewer for Raspberry Pi (Ultimate Tour Edition)
---------------------------------------------------------
Description:
    A professional surveillance dashboard for Raspberry Pi.
    Combines the robustness of the "Professional Edition" with the 
    multi-view capabilities of the "Tour Edition".

Features:
    - 2x2 Grid Layout with Auto-Tour (Cycling).
    - **LIVE PREVIEW** in Admin Panel for camera selection.
    - Full System Stats (CPU, RAM, Disk, Temp, Network Latency).
    - Robust Error Handling & Auto-Recovery.
    - Aggressive Kiosk Mode (Forces Fullscreen on Boot).
    - Auto-Prompt for Admin Password if unconfigured.

Author: Mohammad Hadi Rezaei
License: MIT
"""

import sys
import os
import time
import json
import threading
import subprocess
import math
import re
import tkinter as tk
from tkinter import simpledialog, messagebox, Toplevel, Label, Entry, Button, Checkbutton, IntVar, Frame, LabelFrame
from datetime import datetime

# ==========================================
# 1. DEPENDENCY CHECK
# ==========================================
try:
    import vlc
    import psutil
except ImportError as e:
    root = tk.Tk(); root.withdraw()
    messagebox.showerror("Dependency Error", f"Missing Libs: {e}\nPlease run setup.sh")
    sys.exit(1)

# ==========================================
# 2. CONFIGURATION & DEFAULTS
# ==========================================
CONFIG_FILE = "nvr_config.json"
DEFAULT_CONFIG = {
    "nvr_ip": "192.168.1.108",
    "nvr_port": "554",
    "nvr_user": "admin",
    "nvr_pass": "admin123",
    "subtype": "1",           # 1 = Substream (Recommended for Grid View)
    "admin_pass": "admin",
    "tour_interval": 10,      # Seconds per page
    "active_cameras": []      # List of enabled camera IDs
}

class CameraCell:
    """
    Represents a single quadrant in the 2x2 grid.
    Manages its own VLC player instance and error handling.
    """
    def __init__(self, parent, vlc_instance):
        self.frame = tk.Frame(parent, bg="black", bd=1, relief="sunken")
        self.vlc_instance = vlc_instance
        self.player = None
        
        # Status Text (Center)
        self.status_label = tk.Label(self.frame, text="", bg="black", fg="white", font=("Arial", 12))
        self.status_label.place(relx=0.5, rely=0.5, anchor="center")
        
        # Camera Name Overlay (Top-Left)
        self.name_label = tk.Label(self.frame, text="", bg="black", fg="#00ff00", font=("Arial", 10, "bold"))
        self.name_label.place(relx=0.02, rely=0.02, anchor="nw")

    def play(self, rtsp_url, cam_id):
        """Starts playing a stream in this cell."""
        self.stop() # Ensure clean state
        
        self.name_label.config(text=f"CAM {cam_id}")
        self.status_label.config(text="Connecting...", fg="yellow")
        self.status_label.lift()
        self.name_label.lift()
        
        try:
            self.player = self.vlc_instance.media_player_new()
            media = self.vlc_instance.media_new(rtsp_url)
            self.player.set_media(media)
            
            # Embed player based on OS
            if sys.platform.startswith('linux'):
                self.player.set_xwindow(self.frame.winfo_id())
            else:
                self.player.set_hwnd(self.frame.winfo_id())
                
            self.player.play()
            
            # Assume success initially; monitor loop will catch failures
            self.status_label.lower() 
            
        except Exception as e:
            self.status_label.config(text=f"Init Error: {e}", fg="red")
            self.status_label.lift()

    def stop(self):
        """Stops playback and releases VLC resources to free RAM."""
        if self.player:
            self.player.stop()
            self.player.release()
            self.player = None
        self.status_label.config(text="")
        self.name_label.config(text="")

    def check_health(self):
        """Checks VLC state and updates UI if stream is dead."""
        if self.player:
            state = self.player.get_state()
            if state in [vlc.State.Error, vlc.State.Ended, vlc.State.Stopped]:
                self.status_label.config(text="No Signal", fg="red")
                self.status_label.lift()
            elif state == vlc.State.Playing:
                self.status_label.lower()

class SmartNVRTourApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Smart NVR Tour Viewer")
        self.root.configure(bg="black")
        
        # --- Kiosk Mode Setup (Immediate) ---
        self.root.attributes('-fullscreen', True)
        self.root.attributes('-topmost', True)
        self.root.config(cursor="none")
        
        # Load Config
        self.config = self.load_config()
        self.tour_active = False
        self.current_page_index = 0
        self.active_cam_list = sorted(self.config.get("active_cameras", []))
        
        # System Optimizations
        self.disable_screensaver()
        
        # --- VLC Instance (Shared) ---
        # Optimized for grid playback
        vlc_args = [
            "--no-xlib",
            "--network-caching=300",
            "--rtsp-tcp",
            "--clock-jitter=0",
            "--quiet"
        ]
        self.vlc_instance = vlc.Instance(*vlc_args)
        
        # --- UI Layout ---
        self.cells = []
        self.setup_grid()
        
        # --- Bindings ---
        self.root.bind("<Control-Alt-s>", self.open_admin_panel)
        self.root.bind("<Escape>", self.on_close)
        
        # --- Startup Logic ---
        # If no cameras are configured, prompt admin panel immediately
        if not self.active_cam_list:
            self.root.after(1000, lambda: self.open_admin_panel(force=True))
        else:
            self.start_tour()
            
        # --- Background Tasks ---
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()
        
        # Periodic UI Tasks
        self.enforce_kiosk_mode()

    def setup_grid(self):
        """Creates the 2x2 grid layout."""
        container = tk.Frame(self.root, bg="black")
        container.pack(fill=tk.BOTH, expand=True)
        
        # 2x2 Weights
        container.columnconfigure(0, weight=1); container.columnconfigure(1, weight=1)
        container.rowconfigure(0, weight=1); container.rowconfigure(1, weight=1)
        
        for row in range(2):
            for col in range(2):
                cell = CameraCell(container, self.vlc_instance)
                cell.frame.grid(row=row, column=col, sticky="nsew", padx=1, pady=1)
                self.cells.append(cell)
                
        # Footer Info
        self.info_label = tk.Label(self.root, text="System Ready", bg="#111", fg="#888", font=("Arial", 10))
        self.info_label.pack(side=tk.BOTTOM, fill=tk.X)

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'w') as f: json.dump(DEFAULT_CONFIG, f, indent=4)
            except: pass
            return DEFAULT_CONFIG.copy()
        try:
            with open(CONFIG_FILE, 'r') as f: return json.load(f)
        except: return DEFAULT_CONFIG.copy()

    def save_config(self):
        try:
            with open(CONFIG_FILE, 'w') as f: json.dump(self.config, f, indent=4)
        except: pass

    def disable_screensaver(self):
        if sys.platform.startswith('linux'):
            try:
                subprocess.run(["xset", "s", "off"], check=False)
                subprocess.run(["xset", "-dpms"], check=False)
                subprocess.run(["xset", "s", "noblank"], check=False)
            except: pass

    def enforce_kiosk_mode(self):
        """Aggressively enforces fullscreen."""
        try:
            if not self.root.attributes('-fullscreen'): self.root.attributes('-fullscreen', True)
            self.root.attributes('-topmost', True)
        except: pass
        self.root.after(5000, self.enforce_kiosk_mode)

    def build_rtsp_url(self, channel_id):
        return (f"rtsp://{self.config['nvr_user']}:{self.config['nvr_pass']}@"
                f"{self.config['nvr_ip']}:{self.config['nvr_port']}/"
                f"cam/realmonitor?channel={channel_id}&subtype={self.config['subtype']}")

    # --- Tour Logic ---
    def start_tour(self):
        self.tour_active = True
        self.update_grid()

    def update_grid(self):
        """Cycles to the next 4 cameras."""
        if not self.tour_active or not self.active_cam_list: return

        total_cams = len(self.active_cam_list)
        cameras_per_page = 4
        start_idx = self.current_page_index * cameras_per_page
        current_batch = self.active_cam_list[start_idx : start_idx + cameras_per_page]
        
        # Update Info Footer
        page_num = self.current_page_index + 1
        total_pages = math.ceil(total_cams / cameras_per_page)
        self.info_label.config(text=f"Page {page_num}/{total_pages} | Cams: {current_batch} | Interval: {self.config['tour_interval']}s")

        for i, cell in enumerate(self.cells):
            if i < len(current_batch):
                cam_id = current_batch[i]
                url = self.build_rtsp_url(cam_id)
                cell.play(url, cam_id)
            else:
                cell.stop()
                cell.status_label.config(text="EMPTY SLOT", fg="gray")
                cell.status_label.lift()

        # Schedule next
        interval_ms = int(self.config.get("tour_interval", 10)) * 1000
        self.root.after(interval_ms, self.next_page)

    def next_page(self):
        if not self.active_cam_list: return
        total_pages = math.ceil(len(self.active_cam_list) / 4)
        self.current_page_index += 1
        if self.current_page_index >= total_pages:
            self.current_page_index = 0
        self.update_grid()

    def monitor_loop(self):
        """Watchdog for cell health."""
        while True:
            for cell in self.cells: cell.check_health()
            time.sleep(2)

    # ==========================================
    # ADMIN DASHBOARD & PREVIEW
    # ==========================================
    def open_admin_panel(self, event=None, force=False):
        self.root.config(cursor="arrow")
        
        # If forced (startup), don't ask for password immediately if you prefer, 
        # but standard security implies we should.
        pwd = simpledialog.askstring("Admin", "Enter Admin Password:", parent=self.root, show='*')
        
        if pwd == self.config["admin_pass"]:
            self.show_dashboard()
        else:
            if force:
                messagebox.showerror("Error", "Authentication Failed. Exiting.")
                sys.exit(0)
            self.root.config(cursor="none")

    def show_dashboard(self):
        dash = Toplevel(self.root)
        dash.title("System Configuration")
        dash.geometry("900x700")
        dash.configure(bg="#1a1a1a")
        dash.attributes('-topmost', True)
        
        def on_close():
            # Stop preview player if running
            if hasattr(dash, 'preview_player') and dash.preview_player:
                dash.preview_player.stop()
            self.root.config(cursor="none")
            dash.destroy()
        dash.protocol("WM_DELETE_WINDOW", on_close)

        # --- Layout Columns ---
        col_left = Frame(dash, bg="#1a1a1a", width=250); col_left.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        col_mid = Frame(dash, bg="#1a1a1a", width=300); col_mid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        col_right = Frame(dash, bg="#1a1a1a", width=300); col_right.pack(side=tk.RIGHT, fill=tk.Y, padx=10, pady=10)

        # ====================
        # LEFT: SYSTEM STATS
        # ====================
        lbl_stats = LabelFrame(col_left, text=" System Health ", bg="#1a1a1a", fg="#00ff00", font=("Arial", 10, "bold"))
        lbl_stats.pack(fill=tk.X)
        
        stats_labels = {}
        for k in ["cpu", "ram", "temp", "disk", "net", "ping"]:
            l = Label(lbl_stats, text=f"{k.upper()}: ...", bg="#1a1a1a", fg="white", font=("Monospace", 9), anchor="w")
            l.pack(fill=tk.X, padx=5, pady=2)
            stats_labels[k] = l

        def get_ping_ms():
            try:
                # Check latency to NVR
                res = subprocess.run(["ping", "-c", "1", "-W", "1", self.config['nvr_ip']], 
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if res.returncode == 0:
                    match = re.search(r'time[=<]([\d\.]+)', res.stdout)
                    return f"{match.group(1)} ms" if match else "<1 ms"
                return "Timeout"
            except: return "Error"

        def update_stats_loop():
            if not dash.winfo_exists(): return
            try:
                stats_labels['cpu'].config(text=f"CPU: {psutil.cpu_percent()}%")
                stats_labels['ram'].config(text=f"RAM: {psutil.virtual_memory().percent}%")
                stats_labels['disk'].config(text=f"DSK: {psutil.disk_usage('/').percent}%")
                
                temp = "N/A"
                if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
                    with open("/sys/class/thermal/thermal_zone0/temp") as f:
                        temp = f"{int(f.read())/1000:.1f}C"
                stats_labels['temp'].config(text=f"TMP: {temp}")
                
                stats_labels['ping'].config(text=f"PNG: {get_ping_ms()}")
                
                net = psutil.net_io_counters()
                mb_recv = net.bytes_recv / (1024*1024)
                stats_labels['net'].config(text=f"NET: {mb_recv:.1f} MB")
            except: pass
            dash.after(2000, update_stats_loop)
        update_stats_loop()

        # ====================
        # MIDDLE: CONFIGURATION
        # ====================
        lbl_conf = LabelFrame(col_mid, text=" Connection Settings ", bg="#1a1a1a", fg="#00ccff", font=("Arial", 10, "bold"))
        lbl_conf.pack(fill=tk.X, pady=(0, 20))
        
        entries = {}
        fields = [
            ("NVR IP", "nvr_ip"), ("Port", "nvr_port"), 
            ("User", "nvr_user"), ("Pass", "nvr_pass"), 
            ("Interval (s)", "tour_interval")
        ]
        
        for i, (txt, key) in enumerate(fields):
            Label(lbl_conf, text=txt, bg="#1a1a1a", fg="white").grid(row=i, column=0, sticky="e", padx=5, pady=5)
            e = Entry(lbl_conf, bg="#333", fg="white", insertbackground="white")
            e.insert(0, str(self.config.get(key, "")))
            e.grid(row=i, column=1, sticky="ew", padx=5)
            entries[key] = e

        # ====================
        # RIGHT: CAM SELECTOR & PREVIEW
        # ====================
        lbl_preview = LabelFrame(col_right, text=" Live Preview ", bg="#1a1a1a", fg="yellow", font=("Arial", 10, "bold"))
        lbl_preview.pack(fill=tk.X)
        
        # Preview Frame
        preview_frame = Frame(lbl_preview, bg="black", height=150)
        preview_frame.pack(fill=tk.X, padx=5, pady=5)
        preview_frame.pack_propagate(False) # Force height
        
        preview_label = Label(preview_frame, text="Select a camera\nto preview", bg="black", fg="gray")
        preview_label.place(relx=0.5, rely=0.5, anchor="center")

        # Setup separate VLC for preview
        dash.preview_player = self.vlc_instance.media_player_new()
        if sys.platform.startswith('linux'):
            dash.preview_player.set_xwindow(preview_frame.winfo_id())
        else:
            dash.preview_player.set_hwnd(preview_frame.winfo_id())

        def show_preview(cam_id):
            """Plays selected camera in small frame."""
            # Update Settings from inputs first so we use correct credentials
            user = entries['nvr_user'].get()
            pwd = entries['nvr_pass'].get()
            ip = entries['nvr_ip'].get()
            port = entries['nvr_port'].get()
            
            url = f"rtsp://{user}:{pwd}@{ip}:{port}/cam/realmonitor?channel={cam_id}&subtype=1"
            
            dash.preview_player.stop()
            media = self.vlc_instance.media_new(url)
            dash.preview_player.set_media(media)
            dash.preview_player.play()
            preview_label.lower()

        # Camera Grid
        lbl_cams = LabelFrame(col_right, text=" Active Cameras ", bg="#1a1a1a", fg="#00ff00", font=("Arial", 10, "bold"))
        lbl_cams.pack(fill=tk.BOTH, expand=True, pady=10)

        chk_vars = {}
        current_active = self.config.get("active_cameras", [])
        
        # Grid 4x8
        for i in range(1, 33):
            var = IntVar(value=1 if i in current_active else 0)
            chk_vars[i] = var
            
            # Using Button that acts as Checkbox + Preview Trigger
            def on_cam_click(c=i, v=var):
                # Toggle variable
                v.set(1 if v.get() == 0 else 0)
                # Update visual state
                btn = buttons[c]
                if v.get(): btn.config(bg="#00aa00", relief="sunken")
                else: btn.config(bg="#444", relief="raised")
                # Trigger Preview
                show_preview(c)

            btn = Button(lbl_cams, text=f"{i}", bg="#00aa00" if i in current_active else "#444",
                         fg="white", width=3, command=lambda c=i, v=var: on_cam_click(c, v))
            
            row = (i-1) // 4
            col = (i-1) % 4
            btn.grid(row=row, column=col, padx=2, pady=2)
            
            # Save reference to update style
            if not hasattr(dash, 'cam_buttons'): dash.cam_buttons = {}
            buttons = dash.cam_buttons
            buttons[i] = btn

        # --- Footer Actions ---
        def save_and_restart():
            # Save Config Inputs
            for k, e in entries.items():
                val = e.get()
                if k == "tour_interval":
                    try: val = int(val)
                    except: val = 10
                self.config[k] = val
            
            # Save Active Cams
            new_active = [c for c, v in chk_vars.items() if v.get() == 1]
            self.config["active_cameras"] = new_active
            self.active_cam_list = sorted(new_active)
            
            self.save_config()
            messagebox.showinfo("Saved", "Configuration updated.\nSystem will restart tour.")
            on_close()
            
            # Restart Logic
            self.current_page_index = 0
            self.update_grid()

        btn_frame = Frame(dash, bg="#1a1a1a")
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=10)
        
        Button(btn_frame, text="SAVE & RESTART", command=save_and_restart, bg="green", fg="white", font=("Arial", 12, "bold")).pack(side=tk.LEFT, padx=20, expand=True, fill=tk.X)
        Button(btn_frame, text="EXIT APP", command=self.on_close, bg="#cc0000", fg="white", font=("Arial", 12)).pack(side=tk.RIGHT, padx=20)

    def on_close(self, event=None):
        for cell in self.cells: cell.stop()
        self.root.destroy()
        sys.exit(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = SmartNVRTourApp(root)
    root.mainloop()
