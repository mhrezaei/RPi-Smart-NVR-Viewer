#!/usr/bin/env python3
"""
Smart NVR Viewer for Raspberry Pi (Final Ultimate Edition)
----------------------------------------------------------
Description:
    A professional surveillance dashboard designed for Raspberry Pi.
    It overcomes hardware limitations by cycling through cameras in 
    configurable grid layouts (4, 6, 8, 9, 16).

Features:
    - **Variable Grid Sizes:** 4, 6, 8, 9, 16 cameras per page.
    - **Random Fill:** Automatically fills empty slots with random cameras.
    - **Timer Fix:** Prevents rapid cycling by cancelling old timers.
    - **Auto-Aspect Ratio:** Forces 16:9 rendering.
    - **Live Preview:** Visual camera selection in Admin Dashboard.
    - **System Stats:** Real-time CPU, RAM, Disk, Net, Ping monitoring.

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
import random
import tkinter as tk
from tkinter import simpledialog, messagebox, Toplevel, Label, Entry, Button, IntVar, Frame, LabelFrame, OptionMenu, StringVar
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
    "subtype": "1",           # 0=Main, 1=Sub (Default 1 for performance)
    "admin_pass": "admin",
    "tour_interval": 10,      # Seconds per page
    "grid_size": 4,           # Number of cameras per page
    "active_cameras": []      # List of enabled camera IDs
}

class CameraCell:
    """
    Represents a single video slot in the grid.
    Manages VLC player and overlay information.
    """
    def __init__(self, parent, vlc_instance):
        self.frame = tk.Frame(parent, bg="black", bd=1, relief="sunken")
        self.vlc_instance = vlc_instance
        self.player = None
        
        # Center Status Text
        self.status_label = tk.Label(self.frame, text="", bg="black", fg="white", font=("Arial", 12))
        self.status_label.place(relx=0.5, rely=0.5, anchor="center")
        
        # Camera Name Overlay
        self.name_label = tk.Label(self.frame, text="", bg="black", fg="#00ff00", font=("Arial", 10, "bold"))
        self.name_label.place(relx=0.02, rely=0.02, anchor="nw")

    def play(self, rtsp_url, cam_id, is_filler=False):
        """Starts playing a stream in this cell."""
        self.stop() # Ensure clean state
        
        # Mark random fillers visually (optional, can remove "(R)" if preferred)
        display_name = f"CAM {cam_id}" # + (" (R)" if is_filler else "")
        
        self.name_label.config(text=display_name, fg="#ffff00" if is_filler else "#00ff00")
        self.status_label.config(text="Loading...", fg="yellow")
        self.status_label.lift()
        self.name_label.lift()
        
        try:
            self.player = self.vlc_instance.media_player_new()
            media = self.vlc_instance.media_new(rtsp_url)
            self.player.set_media(media)
            
            if sys.platform.startswith('linux'):
                self.player.set_xwindow(self.frame.winfo_id())
            else:
                self.player.set_hwnd(self.frame.winfo_id())
                
            self.player.play()
            self.status_label.lower() 
            
        except Exception as e:
            self.status_label.config(text=f"Error: {e}", fg="red")
            self.status_label.lift()

    def stop(self):
        """Stops playback and releases VLC resources."""
        if self.player:
            self.player.stop()
            self.player.release()
            self.player = None
        self.status_label.config(text="")
        self.name_label.config(text="")

    def check_health(self):
        """Checks VLC state."""
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
        
        # --- Kiosk Mode Setup ---
        self.root.attributes('-fullscreen', True)
        self.root.attributes('-topmost', True)
        self.root.config(cursor="none")
        
        # Load Config
        self.config = self.load_config()
        self.tour_active = False
        self.current_page_index = 0
        self.active_cam_list = sorted(self.config.get("active_cameras", []))
        self.tour_timer = None # CRITICAL: To prevent timer stacking
        
        # System Optimizations
        self.disable_screensaver()
        
        # --- VLC Instance ---
        vlc_args = [
            "--no-xlib",
            "--network-caching=300",
            "--rtsp-tcp",
            "--clock-jitter=0",
            "--aspect-ratio=16:9", 
            "--quiet"
        ]
        self.vlc_instance = vlc.Instance(*vlc_args)
        
        # --- UI Layout ---
        self.cells = []
        self.cells_per_page = 4
        self.grid_container = tk.Frame(self.root, bg="black")
        self.grid_container.pack(fill=tk.BOTH, expand=True)
        
        self.info_label = tk.Label(self.root, text="System Ready", bg="#111", fg="#888", font=("Arial", 10))
        self.info_label.pack(side=tk.BOTTOM, fill=tk.X)

        self.setup_grid_layout()
        
        # --- Bindings ---
        self.root.bind("<Control-Alt-s>", self.open_admin_panel)
        self.root.bind("<Escape>", self.on_close)
        
        # --- Startup ---
        if not self.active_cam_list:
            self.root.after(1000, lambda: self.open_admin_panel(force=True))
        else:
            self.start_tour()
            
        # --- Background Tasks ---
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()
        self.enforce_kiosk_mode()

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
        try:
            if not self.root.attributes('-fullscreen'): self.root.attributes('-fullscreen', True)
            self.root.attributes('-topmost', True)
        except: pass
        self.root.after(5000, self.enforce_kiosk_mode)

    def build_rtsp_url(self, channel_id):
        subtype = self.config.get('subtype', '1')
        return (f"rtsp://{self.config['nvr_user']}:{self.config['nvr_pass']}@"
                f"{self.config['nvr_ip']}:{self.config['nvr_port']}/"
                f"cam/realmonitor?channel={channel_id}&subtype={subtype}")

    # --- Grid Management ---
    def setup_grid_layout(self):
        """Dynamically creates the grid based on config."""
        for cell in self.cells:
            cell.stop()
            cell.frame.destroy()
        self.cells = []
        
        target_size = int(self.config.get("grid_size", 4))
        
        if target_size == 4: rows, cols = 2, 2
        elif target_size == 6: rows, cols = 2, 3
        elif target_size == 8: rows, cols = 3, 3 # 3x3 for aspect ratio
        elif target_size == 9: rows, cols = 3, 3
        elif target_size == 16: rows, cols = 4, 4
        else: rows, cols = 2, 2
            
        for r in range(rows):
            self.grid_container.rowconfigure(r, weight=1)
        for c in range(cols):
            self.grid_container.columnconfigure(c, weight=1)
            
        for r in range(rows):
            for c in range(cols):
                cell = CameraCell(self.grid_container, self.vlc_instance)
                cell.frame.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
                self.cells.append(cell)
        
        self.cells_per_page = len(self.cells)

    # --- Tour Logic ---
    def start_tour(self):
        self.tour_active = True
        self.update_grid_content()

    def stop_tour_timer(self):
        """Cancels pending page turn to prevent rapid cycling."""
        if self.tour_timer:
            try:
                self.root.after_cancel(self.tour_timer)
            except: pass
            self.tour_timer = None

    def update_grid_content(self):
        """Loads cameras for the current page."""
        # Safety Stop of existing timer
        self.stop_tour_timer()

        if not self.tour_active or not self.active_cam_list: return

        total_cams = len(self.active_cam_list)
        limit = self.cells_per_page
        
        start_idx = self.current_page_index * limit
        current_batch = self.active_cam_list[start_idx : start_idx + limit]
        
        # --- Random Fill Logic ---
        needed = limit - len(current_batch)
        if needed > 0 and len(self.active_cam_list) > len(current_batch):
            # Find candidates not in current batch
            candidates = [c for c in self.active_cam_list if c not in current_batch]
            # If candidates are scarce, just reuse active list
            if len(candidates) < needed:
                candidates = self.active_cam_list
            
            # Select random fillers
            if candidates:
                # Use choices to allow repeats if candidates < needed
                fillers = random.choices(candidates, k=needed)
                # We store them as a tuple (id, is_filler) for processing
                display_batch = [(c, False) for c in current_batch] + [(c, True) for c in fillers]
            else:
                display_batch = [(c, False) for c in current_batch]
        else:
            display_batch = [(c, False) for c in current_batch]

        # Update Info Footer
        page_num = self.current_page_index + 1
        total_pages = math.ceil(total_cams / limit)
        self.info_label.config(text=f"Page {page_num}/{total_pages} | Cams: {len(current_batch)} (+{needed} Fillers) | Interval: {self.config['tour_interval']}s")

        for i, cell in enumerate(self.cells):
            if i < len(display_batch):
                cam_id, is_filler = display_batch[i]
                url = self.build_rtsp_url(cam_id)
                cell.play(url, cam_id, is_filler)
            else:
                cell.stop()
                cell.status_label.config(text="EMPTY", fg="#333")
                cell.status_label.lift()

        # Schedule next page
        interval_ms = int(self.config.get("tour_interval", 10)) * 1000
        self.tour_timer = self.root.after(interval_ms, self.next_page)

    def next_page(self):
        if not self.active_cam_list: return
        total_pages = math.ceil(len(self.active_cam_list) / self.cells_per_page)
        self.current_page_index += 1
        if self.current_page_index >= total_pages:
            self.current_page_index = 0
        self.update_grid_content()

    def monitor_loop(self):
        while True:
            for cell in self.cells: cell.check_health()
            time.sleep(2)

    # ==========================================
    # ADMIN DASHBOARD
    # ==========================================
    def open_admin_panel(self, event=None, force=False):
        self.root.config(cursor="arrow")
        pwd = simpledialog.askstring("Admin", "Enter Admin Password:", parent=self.root, show='*')
        
        if pwd == self.config["admin_pass"]:
            self.show_dashboard()
        else:
            if force:
                messagebox.showerror("Error", "Auth Failed. Exiting.")
                sys.exit(0)
            self.root.config(cursor="none")

    def show_dashboard(self):
        dash = Toplevel(self.root)
        dash.title("System Configuration")
        dash.geometry("900x750")
        dash.configure(bg="#1a1a1a")
        dash.attributes('-topmost', True)
        
        def on_close():
            if hasattr(dash, 'preview_player') and dash.preview_player:
                dash.preview_player.stop()
            self.root.config(cursor="none")
            dash.destroy()
        dash.protocol("WM_DELETE_WINDOW", on_close)

        col_left = Frame(dash, bg="#1a1a1a", width=250); col_left.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        col_mid = Frame(dash, bg="#1a1a1a", width=300); col_mid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        col_right = Frame(dash, bg="#1a1a1a", width=300); col_right.pack(side=tk.RIGHT, fill=tk.Y, padx=10, pady=10)

        # LEFT: STATS
        lbl_stats = LabelFrame(col_left, text=" System Health ", bg="#1a1a1a", fg="#00ff00", font=("Arial", 10, "bold"))
        lbl_stats.pack(fill=tk.X)
        stats_labels = {}
        for k in ["cpu", "ram", "temp", "disk", "net", "ping"]:
            l = Label(lbl_stats, text=f"{k.upper()}: ...", bg="#1a1a1a", fg="white", font=("Monospace", 9), anchor="w")
            l.pack(fill=tk.X, padx=5, pady=2); stats_labels[k] = l

        def get_ping_ms():
            try:
                res = subprocess.run(["ping", "-c", "1", "-W", "1", self.config['nvr_ip']], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
                    with open("/sys/class/thermal/thermal_zone0/temp") as f: temp = f"{int(f.read())/1000:.1f}C"
                stats_labels['temp'].config(text=f"TMP: {temp}")
                stats_labels['ping'].config(text=f"PNG: {get_ping_ms()}")
                net = psutil.net_io_counters(); mb_recv = net.bytes_recv / (1024*1024)
                stats_labels['net'].config(text=f"NET: {mb_recv:.1f} MB")
            except: pass
            dash.after(2000, update_stats_loop)
        update_stats_loop()

        # MIDDLE: CONFIG
        lbl_conf = LabelFrame(col_mid, text=" Settings ", bg="#1a1a1a", fg="#00ccff", font=("Arial", 10, "bold"))
        lbl_conf.pack(fill=tk.X, pady=(0, 20))
        entries = {}
        fields = [("NVR IP", "nvr_ip"), ("Port", "nvr_port"), ("User", "nvr_user"), ("Pass", "nvr_pass"), ("Interval (s)", "tour_interval")]
        for i, (txt, key) in enumerate(fields):
            Label(lbl_conf, text=txt, bg="#1a1a1a", fg="white").grid(row=i, column=0, sticky="e", padx=5, pady=5)
            e = Entry(lbl_conf, bg="#333", fg="white", insertbackground="white")
            e.insert(0, str(self.config.get(key, "")))
            e.grid(row=i, column=1, sticky="ew", padx=5)
            entries[key] = e

        Label(lbl_conf, text="Grid Size", bg="#1a1a1a", fg="white").grid(row=len(fields), column=0, sticky="e", padx=5)
        grid_var = StringVar(dash); grid_var.set(str(self.config.get("grid_size", 4)))
        OptionMenu(lbl_conf, grid_var, "4", "6", "8", "9", "16").grid(row=len(fields), column=1, sticky="ew")

        Label(lbl_conf, text="Stream Quality", bg="#1a1a1a", fg="white").grid(row=len(fields)+1, column=0, sticky="e", padx=5)
        sub_var = StringVar(dash)
        sub_var.set("Sub Stream (Fast)" if str(self.config.get("subtype", "1")) == "1" else "Main Stream (HD)")
        OptionMenu(lbl_conf, sub_var, "Main Stream (HD)", "Sub Stream (Fast)").grid(row=len(fields)+1, column=1, sticky="ew")

        # RIGHT: CAM SELECTOR
        lbl_preview = LabelFrame(col_right, text=" Preview ", bg="#1a1a1a", fg="yellow", font=("Arial", 10, "bold"))
        lbl_preview.pack(fill=tk.X)
        preview_frame = Frame(lbl_preview, bg="black", height=150)
        preview_frame.pack(fill=tk.X, padx=5, pady=5); preview_frame.pack_propagate(False)
        preview_label = Label(preview_frame, text="Select cam", bg="black", fg="gray")
        preview_label.place(relx=0.5, rely=0.5, anchor="center")

        dash.preview_player = self.vlc_instance.media_player_new()
        if sys.platform.startswith('linux'): dash.preview_player.set_xwindow(preview_frame.winfo_id())
        else: dash.preview_player.set_hwnd(preview_frame.winfo_id())

        def show_preview(cam_id):
            user = entries['nvr_user'].get(); pwd = entries['nvr_pass'].get()
            ip = entries['nvr_ip'].get(); port = entries['nvr_port'].get()
            url = f"rtsp://{user}:{pwd}@{ip}:{port}/cam/realmonitor?channel={cam_id}&subtype=1"
            dash.preview_player.stop()
            media = self.vlc_instance.media_new(url)
            dash.preview_player.set_media(media)
            dash.preview_player.play()
            preview_label.lower()

        lbl_cams = LabelFrame(col_right, text=" Active Cameras ", bg="#1a1a1a", fg="#00ff00", font=("Arial", 10, "bold"))
        lbl_cams.pack(fill=tk.BOTH, expand=True, pady=10)
        chk_vars = {}; current_active = self.config.get("active_cameras", [])
        
        for i in range(1, 33):
            var = IntVar(value=1 if i in current_active else 0); chk_vars[i] = var
            def on_cam_click(c=i, v=var):
                v.set(1 if v.get() == 0 else 0)
                btn = buttons[c]
                btn.config(bg="#00aa00" if v.get() else "#444", relief="sunken" if v.get() else "raised")
                show_preview(c)
            btn = Button(lbl_cams, text=f"{i}", bg="#00aa00" if i in current_active else "#444", fg="white", width=3, command=lambda c=i, v=var: on_cam_click(c, v))
            btn.grid(row=(i-1)//4, column=(i-1)%4, padx=2, pady=2)
            if not hasattr(dash, 'cam_buttons'): dash.cam_buttons = {}
            buttons = dash.cam_buttons; buttons[i] = btn

        def save_and_restart():
            # Stop existing timer immediately
            self.stop_tour_timer()
            
            for k, e in entries.items():
                if k == "tour_interval":
                    try: self.config[k] = int(e.get())
                    except: self.config[k] = 10
                else: self.config[k] = e.get()
            
            try: self.config["grid_size"] = int(grid_var.get())
            except: self.config["grid_size"] = 4
            self.config["subtype"] = "1" if "Sub" in sub_var.get() else "0"
            
            new_active = [c for c, v in chk_vars.items() if v.get() == 1]
            self.config["active_cameras"] = new_active
            self.active_cam_list = sorted(new_active)
            self.save_config()
            
            messagebox.showinfo("Saved", "Updating...")
            on_close()
            
            # Restart Tour
            self.current_page_index = 0
            self.setup_grid_layout()
            self.update_grid_content()

        btn_frame = Frame(dash, bg="#1a1a1a")
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=10)
        Button(btn_frame, text="SAVE & RESTART", command=save_and_restart, bg="green", fg="white", font=("Arial", 12, "bold")).pack(side=tk.LEFT, padx=20, expand=True, fill=tk.X)
        Button(btn_frame, text="EXIT APP", command=self.on_close, bg="#cc0000", fg="white", font=("Arial", 12)).pack(side=tk.RIGHT, padx=20)

    def on_close(self, event=None):
        self.stop_tour_timer()
        for cell in self.cells: cell.stop()
        self.root.destroy()
        sys.exit(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = SmartNVRTourApp(root)
    root.mainloop()
