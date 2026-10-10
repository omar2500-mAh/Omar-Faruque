"""FSM AC Voltage Protector desktop dashboard (SimulIDE via com0com).

Install: py -m pip install pyserial matplotlib
Run:     py fsm_dashboard.py
SimulIDE: SerialPort -> COM6, 9600 8N1; Python -> COM5.

This is a monitoring/educational simulation UI, not a safety-rated protection system.
"""
import csv
import queue
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

import serial
from serial.tools import list_ports
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

VOLTAGE_RE = re.compile(r"GRID\s*=\s*([+-]?\d+(?:\.\d+)?)\s*V\s*\|\s*RELAY\s*=\s*(ON|OFF)\s*\|\s*STATE\s*=\s*([A-Z_]+)", re.I)
LIMIT_RE = re.compile(r"UV_LIMIT\s*=\s*([+-]?\d+(?:\.\d+)?)\s*V\s*\|\s*OV_LIMIT\s*=\s*([+-]?\d+(?:\.\d+)?)\s*V", re.I)
LOG_DIR = Path.home() / "FSM_Voltage_Protector_Logs"


class Dashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("FSM Voltage Protector | Live Monitoring")
        self.root.geometry("1160x810")
        self.root.minsize(940, 680)
        self.root.configure(bg="#0e1523")
        self.port = None
        self.worker = None
        self.running = threading.Event()
        self.inbox = queue.Queue()
        self.lock = threading.Lock()
        self.history_t = deque(maxlen=240)
        self.history_v = deque(maxlen=240)
        self.start_time = time.monotonic()
        self.state = "WAITING"
        self.relay = "--"
        self.grid = None
        self.uv = 180.0
        self.ov = 250.0
        self.last_rx = None
        self.log_file = None
        self.log_writer = None
        self.last_state = None

        self.port_var = tk.StringVar(value="COM5")
        self.connection_var = tk.StringVar(value="Disconnected")
        self.grid_var = tk.StringVar(value="--.-- V")
        self.relay_var = tk.StringVar(value="--")
        self.state_var = tk.StringVar(value="WAITING FOR DATA")
        self.uv_var = tk.StringVar(value="180.0 V")
        self.ov_var = tk.StringVar(value="250.0 V")
        self.uv_input = tk.StringVar(value="175")
        self.ov_input = tk.StringVar(value="260")
        self.status_var = tk.StringVar(value="Open SimulIDE COM6, then click Connect COM5.")
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self.poll)

    def _build(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", font=("Segoe UI", 10), padding=8)
        style.configure("TCombobox", padding=5)
        style.configure("TEntry", padding=5)
        bg = "#0e1523"
        panel = "#192336"
        fg = "#eef3fc"
        muted = "#9caec8"

        top = tk.Frame(self.root, bg=bg, padx=20, pady=15)
        top.pack(fill="x")
        tk.Label(top, text="FSM VOLTAGE PROTECTOR", bg=bg, fg=fg,
                 font=("Segoe UI", 20, "bold")).pack(side="left")
        tk.Label(top, text="SIMULIDE  •  COM6 ⇄ COM5  •  9600 BAUD", bg=bg, fg=muted,
                 font=("Segoe UI", 10)).pack(side="right")

        toolbar = tk.Frame(self.root, bg=panel, padx=16, pady=12)
        toolbar.pack(fill="x", padx=18, pady=(0, 12))
        tk.Label(toolbar, text="Python Port", fg=fg, bg=panel).pack(side="left")
        self.combo = ttk.Combobox(toolbar, width=12, textvariable=self.port_var, values=self.available_ports())
        self.combo.pack(side="left", padx=(10, 8))
        ttk.Button(toolbar, text="Refresh ports", command=self.refresh_ports).pack(side="left", padx=4)
        self.connect_button = ttk.Button(toolbar, text="Connect", command=self.toggle_connection)
        self.connect_button.pack(side="left", padx=4)
        tk.Label(toolbar, textvariable=self.connection_var, bg=panel, fg="#68d8b0",
                 font=("Segoe UI", 10, "bold")).pack(side="right")

        cards = tk.Frame(self.root, bg=bg)
        cards.pack(fill="x", padx=18)
        for i in range(4):
            cards.grid_columnconfigure(i, weight=1, uniform="card")
        self._card(cards, 0, "GRID VOLTAGE", self.grid_var, "#80c9ff")
        self._card(cards, 1, "RELAY", self.relay_var, "#9fe3b4")
        self._card(cards, 2, "FSM STATE", self.state_var, "#f9ca85", small=True)
        limits = tk.Frame(cards, bg=panel, padx=18, pady=15)
        limits.grid(row=0, column=3, padx=(8, 0), sticky="nsew")
        tk.Label(limits, text="PROTECTION LIMITS", bg=panel, fg=muted,
                 font=("Segoe UI", 10)).pack(anchor="w")
        tk.Label(limits, textvariable=self.uv_var, bg=panel, fg="#80c9ff",
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(limits, textvariable=self.ov_var, bg=panel, fg="#f9ca85",
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")

        center = tk.Frame(self.root, bg=bg)
        center.pack(fill="both", expand=True, padx=18, pady=12)
        center.grid_columnconfigure(0, weight=3)
        center.grid_columnconfigure(1, weight=2)
        center.grid_rowconfigure(0, weight=1)

        graph_panel = tk.Frame(center, bg=panel, padx=12, pady=10)
        graph_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        tk.Label(graph_panel, text="LIVE GRID VOLTAGE (RMS)", bg=panel, fg=fg,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 7))
        fig = Figure(figsize=(6, 4), facecolor=panel, tight_layout=True)
        self.ax = fig.add_subplot(111)
        self.ax.set_facecolor("#111b2b")
        self.ax.tick_params(colors="#b9c7da", labelsize=9)
        for spine in self.ax.spines.values():
            spine.set_color("#43516a")
        self.ax.set_xlabel("Seconds since connection", color="#b9c7da")
        self.ax.set_ylabel("Voltage (V RMS)", color="#b9c7da")
        self.ax.grid(alpha=0.15, color="white")
        (self.line,) = self.ax.plot([], [], color="#61baff", linewidth=2)
        self.uv_line = self.ax.axhline(self.uv, color="#ffba69", linestyle="--", alpha=.7, label="UV trip")
        self.ov_line = self.ax.axhline(self.ov, color="#ff738a", linestyle="--", alpha=.7, label="OV trip")
        self.canvas = FigureCanvasTkAgg(fig, master=graph_panel)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        ctrl = tk.Frame(center, bg=panel, padx=16, pady=12)
        ctrl.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        tk.Label(ctrl, text="REMOTE CONTROLS", fg=fg, bg=panel,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 12))
        ttk.Button(ctrl, text="Request STATUS", command=lambda: self.send("STATUS")).pack(fill="x", pady=4)
        ttk.Button(ctrl, text="Request HELP", command=lambda: self.send("HELP")).pack(fill="x", pady=4)
        ttk.Button(ctrl, text="RESET Fault (only when voltage is safe)", command=self.confirm_reset).pack(fill="x", pady=6)
        tk.Label(ctrl, text="Overvoltage threshold (V)", fg=muted, bg=panel).pack(anchor="w", pady=(14, 3))
        ttk.Entry(ctrl, textvariable=self.ov_input).pack(fill="x")
        ttk.Button(ctrl, text="Send SET OV", command=self.set_ov).pack(fill="x", pady=6)
        tk.Label(ctrl, text="Undervoltage threshold (V)", fg=muted, bg=panel).pack(anchor="w", pady=(10, 3))
        ttk.Entry(ctrl, textvariable=self.uv_input).pack(fill="x")
        ttk.Button(ctrl, text="Send SET UV", command=self.set_uv).pack(fill="x", pady=6)
        tk.Label(ctrl, text="Commands are validated by Arduino; this GUI cannot override safety rules.",
                 bg=panel, fg=muted, justify="left", wraplength=350,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(16, 0))

        logframe = tk.Frame(self.root, bg=panel, padx=13, pady=9)
        logframe.pack(fill="both", padx=18, pady=(0, 10))
        header = tk.Frame(logframe, bg=panel)
        header.pack(fill="x")
        tk.Label(header, text="EVENTS & SERIAL ACTIVITY", bg=panel, fg=fg,
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(header, text="Open logs folder", command=self.open_logs).pack(side="right")
        self.logbox = tk.Text(logframe, bg="#111b2b", fg="#cdd8eb", height=8, relief="flat",
                              font=("Consolas", 9), wrap="word")
        self.logbox.pack(fill="both", expand=True, pady=(5, 0))
        self.logbox.configure(state="disabled")
        tk.Label(self.root, textvariable=self.status_var, bg=bg, fg=muted,
                 font=("Segoe UI", 9), anchor="w").pack(fill="x", padx=20, pady=(0, 10))

    def _card(self, parent, col, title, var, value_color, small=False):
        frame = tk.Frame(parent, bg="#192336", padx=18, pady=15)
        frame.grid(row=0, column=col, sticky="nsew", padx=(0, 8))
        tk.Label(frame, text=title, bg="#192336", fg="#9caec8",
                 font=("Segoe UI", 10)).pack(anchor="w")
        tk.Label(frame, textvariable=var, bg="#192336", fg=value_color,
                 font=("Segoe UI", 13 if small else 22, "bold"),
                 wraplength=245, justify="left").pack(anchor="w", pady=(12, 0))

    @staticmethod
    def available_ports():
        return sorted(p.device for p in list_ports.comports())

    def refresh_ports(self):
        self.combo.configure(values=self.available_ports())

    def toggle_connection(self):
        if self.running.is_set():
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        port_name = self.port_var.get().strip()
        if not port_name:
            messagebox.showerror("Port missing", "Select COM5 (paired with SimulIDE COM6).")
            return
        try:
            ser = serial.Serial(port_name, 9600, timeout=0.25, write_timeout=1)
        except (serial.SerialException, OSError) as exc:
            messagebox.showerror("COM port error", f"Could not open {port_name}:\n{exc}\n\nClose miniterm or any other program holding this port.")
            return
        self.port = ser
        self.running.set()
        self.start_time = time.monotonic()
        self.history_t.clear()
        self.history_v.clear()
        self.last_state = None
        self.last_rx = None
        self._start_log()
        self.worker = threading.Thread(target=self._serial_worker, daemon=True)
        self.worker.start()
        self.connection_var.set(f"CONNECTED: {port_name}")
        self.connect_button.configure(text="Disconnect")
        self.status_var.set("Connected. Reading Arduino telemetry; commands are available.")
        self.write_log("LOCAL: CONNECTED " + port_name)
        self.send("STATUS")

    def _serial_worker(self):
        buffer = bytearray()
        while self.running.is_set():
            try:
                data = self.port.read(512)
                if not data:
                    continue
                buffer.extend(data)
                while b"\n" in buffer:
                    raw, _, remainder = buffer.partition(b"\n")
                    buffer = bytearray(remainder)
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line:
                        self.inbox.put(("line", line))
                if len(buffer) > 4096:
                    buffer.clear()
            except (serial.SerialException, OSError) as exc:
                if self.running.is_set():
                    self.inbox.put(("error", str(exc)))
                break

    def disconnect(self):
        self.running.clear()
        ser, self.port = self.port, None
        if ser:
            try:
                ser.close()
            except Exception:
                pass
        if self.log_file:
            try:
                self.log_file.flush()
                self.log_file.close()
            except Exception:
                pass
            self.log_file = None
            self.log_writer = None
        self.connection_var.set("Disconnected")
        self.connect_button.configure(text="Connect")
        self.status_var.set("Disconnected; existing readings remain visible.")

    def send(self, command):
        if not self.running.is_set() or self.port is None:
            messagebox.showwarning("Not connected", "Open SimulIDE COM6 and connect the dashboard to COM5 first.")
            return
        try:
            with self.lock:
                self.port.write((command + "\n").encode("ascii"))
            self.write_log("TX: " + command)
        except (serial.SerialException, OSError) as exc:
            messagebox.showerror("Serial send failed", str(exc))

    def confirm_reset(self):
        if messagebox.askyesno("Remote RESET", "Send RESET to Arduino?\n\nArduino will only accept reset if measured voltage is in its configured safe range."):
            self.send("RESET")

    def set_ov(self):
        try:
            value = float(self.ov_input.get())
            if not 210 <= value <= 280:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Invalid threshold", "Overvoltage limit must be between 210 and 280 V.")
            return
        self.send(f"SET OV {value:g}")

    def set_uv(self):
        try:
            value = float(self.uv_input.get())
            if not 100 <= value <= 220:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Invalid threshold", "Undervoltage limit must be between 100 and 220 V.")
            return
        self.send(f"SET UV {value:g}")

    def _start_log(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_path = LOG_DIR / ("fsm_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
        self.log_file = open(file_path, "w", newline="", encoding="utf-8")
        self.log_writer = csv.writer(self.log_file)
        self.log_writer.writerow(["timestamp", "type", "voltage_rms_v", "relay", "fsm_state", "uv_limit_v", "ov_limit_v", "message"])
        self.log_file.flush()
        self.log_path = file_path

    def write_log(self, message):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.logbox.configure(state="normal")
        self.logbox.insert("end", f"[{stamp}] {message}\n")
        self.logbox.see("end")
        if int(self.logbox.index("end-1c").split(".")[0]) > 450:
            self.logbox.delete("1.0", "90.0")
        self.logbox.configure(state="disabled")
        if self.log_writer:
            self.log_writer.writerow([datetime.now().isoformat(timespec="seconds"), "event", self.grid, self.relay, self.state, self.uv, self.ov, message])
            self.log_file.flush()

    def on_line(self, line):
        self.last_rx = time.monotonic()
        m = VOLTAGE_RE.search(line)
        if m:
            self.grid = float(m.group(1))
            self.relay = m.group(2).upper()
            new_state = m.group(3).upper()
            self.grid_var.set(f"{self.grid:.2f} V")
            self.relay_var.set(self.relay)
            self.state_var.set(new_state.replace("_", "\n") if len(new_state) > 17 else new_state)
            self.history_t.append(time.monotonic() - self.start_time)
            self.history_v.append(self.grid)
            if new_state != self.last_state:
                self.write_log(f"FSM STATE: {self.last_state or '--'} → {new_state} | RELAY={self.relay} | GRID={self.grid:.2f}V")
                self.last_state = new_state
            if self.log_writer:
                self.log_writer.writerow([datetime.now().isoformat(timespec="milliseconds"), "reading", self.grid, self.relay, self.state, self.uv, self.ov, line])
                self.log_file.flush()
            self.state = new_state
            return True
        m = LIMIT_RE.search(line)
        if m:
            self.uv, self.ov = float(m.group(1)), float(m.group(2))
            self.uv_var.set(f"UV  {self.uv:.1f} V")
            self.ov_var.set(f"OV  {self.ov:.1f} V")
            return False
        if any(marker in line.upper() for marker in ("TRIP", "RESET", "UPDATED", "DENIED", "ERROR", "STARTUP COMPLETE", "COMMANDS", "READY", "STATUS", "LIMIT")):
            self.write_log("RX: " + line)
        return False

    def poll(self):
        changed = False
        for _ in range(200):
            try:
                typ, val = self.inbox.get_nowait()
            except queue.Empty:
                break
            if typ == "error":
                self.write_log("SERIAL ERROR: " + val)
                self.disconnect()
            elif typ == "line":
                changed = self.on_line(val) or changed
        if changed and self.history_t:
            self.line.set_data(list(self.history_t), list(self.history_v))
            self.uv_line.set_ydata([self.uv, self.uv])
            self.ov_line.set_ydata([self.ov, self.ov])
            xs = list(self.history_t)
            self.ax.set_xlim(max(0, xs[-1] - 120), max(20, xs[-1] + 3))
            values = list(self.history_v) + [self.uv, self.ov]
            self.ax.set_ylim(min(values) - 15, max(values) + 15)
            self.canvas.draw_idle()
        if self.running.is_set() and self.last_rx is not None:
            if time.monotonic() - self.last_rx > 5:
                self.status_var.set("Port open, but no new telemetry for 5+ seconds. Check SimulIDE simulation and COM6.")
            else:
                self.status_var.set(f"Live data • Last update {datetime.now().strftime('%H:%M:%S')} • CSV logs: {LOG_DIR}")
        self.root.after(150, self.poll)

    def open_logs(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        import os
        os.startfile(str(LOG_DIR))  # Windows only

    def close(self):
        self.disconnect()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    Dashboard(root)
    root.mainloop()
