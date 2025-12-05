#!/usr/bin/env python3
"""
Smart NVR Viewer for Raspberry Pi (Tour Edition)
------------------------------------------------
Description:
    A multi-view surveillance system designed for Raspberry Pi 3/4.
    Instead of decoding 32 streams simultaneously (impossible on Pi),
    this app cycles through user-selected cameras in a 2x2 grid layout.

Features:
    - 2x2 Grid Layout (4 Cameras per page)
    - Auto-Cycling Tour Mode (Configurable interval)
    - Selective Camera Enabling (Choose active cameras from 1-32)
    - Admin Dashboard with Visual Camera Selector
    - Memory Leak Protection (Re-initializes players on cycle)
    - Substream optimization for smooth playback

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
import tkinter as tk
from tkinter import simpledialog, messagebox, Toplevel, Label, Entry, Button, Checkbutton, IntVar, Frame
from datetime import datetime

# ==========================================
# 1. DEPENDENCY CHECK
# ==========================================
try:
    import vlc
    import psutil
except ImportError as e:
    # GUI Error for missing libs
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
    "subtype": "1",           # Force Substream (1) for Grid View to prevent lag
    "admin_pass": "admin",
    "tour_interval": 10,      # Seconds per page
    "active_cameras": [1, 2, 3, 4] # Default active cameras
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
        self.status_label = tk.Label(self.frame, text="", bg="black", fg="white", font=("Arial", 10))
        self.status_label.place(relx=0.5, rely=0.5, anchor="center")
        
        # Overlay for Camera Name
        self.name_label = tk.Label(self.frame, text="", bg="black", fg="#00ff00", font=("Arial", 8, "bold"))
        self.name_label.place(relx=0.02, rely=0.02, anchor="nw")

    def play(self, rtsp_url, cam_id):
        """Starts playing a stream in this cell."""
        self.stop() # Clean up previous
        
        self.name_label.config(text=f"CAM {cam_id}")
        self.status_label.config(text="Loading...", fg="yellow")
        self.status_label.lift()
        self.name_label.lift()
        
        try:
            self.player = self.vlc_instance.media_player_new()
            media = self.vlc_instance.media_new(rtsp_url)
            self.player.set_media(media)
            
            # Platform specific embedding
            if sys.platform.startswith('linux'):
                self.player.set_xwindow(self.frame.winfo_id())
            else:
                self.player.set_hwnd(self.frame.winfo_id())
                
            self.player.play()
            
            # Check if playing successfully after a brief delay
            # We do this check in the main loop, but here we just init
            self.status_label.lower() # Hide loading text if it works immediately
            
        except Exception as e:
            self.status_label.config(text=f"Error: {e}", fg="red")
            self.status_label.lift()

    def stop(self):
        """Stops playback and releases resources."""
        if self.player:
            self.player.stop()
            self.player.release()
            self.player = None
        self.status_label.config(text="")

    def check_health(self):
        """Checks if the player is actually playing."""
        if self.player:
            state = self.player.get_state()
            if state in [vlc.State.Error, vlc.State.Ended, vlc.State.Stopped]:
                self.status_label.config(text="No Signal", fg="red")
                self.status_label.lift()
            elif state == vlc.State.Playing:
                self.status_label.lower() # All good

class SmartNVRTourApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Smart NVR Tour Viewer")
        self.root.configure(bg="black")
        
        # Load Config
        self.config = self.load_config()
        self.tour_active = False
        self.current_page_index = 0
        self.active_cam_list = sorted(self.config.get("active_cameras", []))
        
        # Optimization
        self.disable_screensaver()
        self.root.attributes('-fullscreen', True)
        self.root.attributes('-topmost', True)
        self.root.config(cursor="none")
        
        # --- VLC Instance (Shared) ---
        # Optimized for multiple low-res streams
        vlc_args = [
            "--no-xlib",
            "--network-caching=300",
            "--rtsp-tcp",
            "--quiet"
        ]
        self.vlc_instance = vlc.Instance(*vlc_args)
        
        # --- UI Layout (2x2 Grid) ---
        self.cells = []
        self.setup_grid()
        
        # --- Bindings ---
        self.root.bind("<Control-Alt-s>", self.open_admin_panel)
        self.root.bind("<Escape>", self.on_close)
        
        # --- Start Tour ---
        if not self.active_cam_list:
            messagebox.showwarning("No Cameras", "No cameras selected! Press Ctrl+Alt+S to configure.")
        else:
            self.start_tour()
            
        # --- Background Monitor ---
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()

    def setup_grid(self):
        """Creates the 4 quadrants."""
        # Main container
        container = tk.Frame(self.root, bg="black")
        container.pack(fill=tk.BOTH, expand=True)
        
        # Configure 2x2 grid weight
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)
        container.rowconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)
        
        # Create 4 cells
        for row in range(2):
            for col in range(2):
                cell = CameraCell(container, self.vlc_instance)
                cell.frame.grid(row=row, column=col, sticky="nsew", padx=1, pady=1)
                self.cells.append(cell)
                
        # Info Bar (Bottom)
        self.info_label = tk.Label(self.root, text="Tour Mode Active", bg="#111", fg="#888", font=("Arial", 10))
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

    def build_rtsp_url(self, channel_id):
        """Generates URL for a specific channel."""
        return (f"rtsp://{self.config['nvr_user']}:{self.config['nvr_pass']}@"
                f"{self.config['nvr_ip']}:{self.config['nvr_port']}/"
                f"cam/realmonitor?channel={channel_id}&subtype={self.config['subtype']}")

    def start_tour(self):
        """Initializes the tour loop."""
        self.tour_active = True
        self.update_grid()

    def update_grid(self):
        """Loads the next batch of 4 cameras."""
        if not self.tour_active or not self.active_cam_list:
            return

        # Calculate slices
        total_cams = len(self.active_cam_list)
        cameras_per_page = 4
        start_idx = self.current_page_index * cameras_per_page
        
        # Get the sub-list for this page
        current_batch = self.active_cam_list[start_idx : start_idx + cameras_per_page]
        
        # Update Info Bar
        page_num = self.current_page_index + 1
        total_pages = math.ceil(total_cams / cameras_per_page)
        self.info_label.config(text=f"Page {page_num}/{total_pages} | Cameras: {current_batch} | Next cycle in {self.config['tour_interval']}s")

        # Assign streams to cells
        for i, cell in enumerate(self.cells):
            if i < len(current_batch):
                cam_id = current_batch[i]
                url = self.build_rtsp_url(cam_id)
                cell.play(url, cam_id)
            else:
                # If we have fewer than 4 cams on this page, stop unused cells
                cell.stop()
                cell.status_label.config(text="EMPTY", fg="gray")
                cell.status_label.lift()
                cell.name_label.config(text="")

        # Schedule next cycle
        interval_ms = int(self.config.get("tour_interval", 10)) * 1000
        self.root.after(interval_ms, self.next_page)

    def next_page(self):
        """Calculates index for next page and triggers update."""
        if not self.active_cam_list: return
        
        total_cams = len(self.active_cam_list)
        cameras_per_page = 4
        max_pages = math.ceil(total_cams / cameras_per_page)
        
        self.current_page_index += 1
        if self.current_page_index >= max_pages:
            self.current_page_index = 0 # Loop back to start
            
        self.update_grid()

    def monitor_loop(self):
        """Checks health of active cells every 2 seconds."""
        while True:
            for cell in self.cells:
                cell.check_health()
            time.sleep(2)

    # ==========================================
    # ADMIN PANEL (CAMERA SELECTOR)
    # ==========================================
    def open_admin_panel(self, event=None):
        self.root.config(cursor="arrow")
        pwd = simpledialog.askstring("Admin", "Password:", parent=self.root, show='*')
        if pwd == self.config["admin_pass"]:
            self.show_dashboard()
        else:
            self.root.config(cursor="none")

    def show_dashboard(self):
        dash = Toplevel(self.root)
        dash.title("Tour Config")
        dash.geometry("800x600")
        dash.configure(bg="#222")
        dash.attributes('-topmost', True)
        
        def on_close():
            self.root.config(cursor="none")
            dash.destroy()
        dash.protocol("WM_DELETE_WINDOW", on_close)

        # --- Settings Header ---
        Label(dash, text="General Settings", bg="#222", fg="#00ccff", font=("Arial", 12, "bold")).pack(pady=5)
        
        # Input Frame
        frm_inputs = Frame(dash, bg="#222")
        frm_inputs.pack(pady=5)
        
        entries = {}
        fields = [("IP", "nvr_ip"), ("User", "nvr_user"), ("Pass", "nvr_pass"), ("Interval (Sec)", "tour_interval")]
        
        for i, (lbl, key) in enumerate(fields):
            Label(frm_inputs, text=lbl, bg="#222", fg="white").grid(row=0, column=i*2, padx=5)
            e = Entry(frm_inputs, width=15)
            e.insert(0, str(self.config.get(key, "")))
            e.grid(row=0, column=i*2+1, padx=5)
            entries[key] = e

        # --- Camera Grid Selector ---
        Label(dash, text="Select Active Cameras", bg="#222", fg="#00ff00", font=("Arial", 12, "bold")).pack(pady=10)
        
        frm_grid = Frame(dash, bg="#333", padx=10, pady=10)
        frm_grid.pack()
        
        self.chk_vars = {}
        current_active = self.config.get("active_cameras", [])
        
        # 4 rows of 8 columns = 32 cameras
        for i in range(1, 33):
            var = IntVar(value=1 if i in current_active else 0)
            self.chk_vars[i] = var
            
            # Button Logic:
            # We use Checkbuttons for selection logic, but styled simply
            chk = Checkbutton(frm_grid, text=f"{i:02d}", variable=var, 
                              bg="#444", fg="white", selectcolor="#00aa00",
                              activebackground="#555", activeforeground="white",
                              indicatoron=0, width=4, height=2)
            
            row = (i-1) // 8
            col = (i-1) % 8
            chk.grid(row=row, column=col, padx=2, pady=2)

        # --- Save & Actions ---
        def save_changes():
            # Update General Config
            for k, e in entries.items():
                if k == "tour_interval":
                    try: self.config[k] = int(e.get())
                    except: self.config[k] = 10
                else:
                    self.config[k] = e.get()
            
            # Update Active List
            new_active = [cam_id for cam_id, var in self.chk_vars.items() if var.get() == 1]
            self.config["active_cameras"] = new_active
            self.active_cam_list = sorted(new_active)
            
            self.save_config()
            messagebox.showinfo("Saved", "Configuration updated!\nTour will restart.")
            on_close()
            
            # Restart Tour Logic
            self.current_page_index = 0
            self.active_cam_list = sorted(new_active)
            self.update_grid() # Trigger immediate update

        Button(dash, text="SAVE & RESTART TOUR", command=save_changes, bg="green", fg="white", font=("Arial", 12), width=30).pack(pady=20)
        Button(dash, text="EXIT APP", command=self.on_close, bg="red", fg="white").pack(pady=5)

    def on_close(self, event=None):
        for cell in self.cells: cell.stop()
        self.root.destroy()
        sys.exit(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = SmartNVRTourApp(root)
    root.mainloop()
