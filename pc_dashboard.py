"""A lightweight, dependency-free desktop dashboard for basic PC stats."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import socket
import sys
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk


BG = "#10151f"
PANEL = "#192231"
MUTED = "#9aa8bb"
TEXT = "#f2f6fc"
ACCENT = "#72e0b1"
BLUE = "#73b7ff"


class MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class FileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    def value(self) -> int:
        return (self.dwHighDateTime << 32) | self.dwLowDateTime


def memory_stats() -> tuple[int, int]:
    """Return used and total physical memory in bytes."""
    if sys.platform == "win32":
        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("Windows could not read memory status")
        return status.ullTotalPhys - status.ullAvailPhys, status.ullTotalPhys

    if sys.platform.startswith("linux"):
        values: dict[str, int] = {}
        with open("/proc/meminfo", encoding="ascii") as meminfo:
            for line in meminfo:
                key, value = line.split(":", 1)
                if key in {"MemTotal", "MemAvailable"}:
                    values[key] = int(value.strip().split()[0]) * 1024
        total = values["MemTotal"]
        return total - values["MemAvailable"], total

    if sys.platform == "darwin":
        import subprocess

        total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip())
        page_size = os.sysconf("SC_PAGE_SIZE")
        free_pages = os.sysconf("SC_AVPHYS_PAGES")
        return total - page_size * free_pages, total

    raise OSError("Memory stats are not available on this platform")


class CpuSampler:
    """Calculate CPU use from two cumulative OS counter samples."""

    def __init__(self) -> None:
        self.previous: tuple[int, int] | None = None

    def usage(self) -> float:
        if sys.platform == "win32":
            idle, kernel, user = FileTime(), FileTime(), FileTime()
            if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            ):
                raise OSError("Windows could not read CPU times")
            sample = (kernel.value() + user.value(), idle.value())
        elif sys.platform.startswith("linux"):
            with open("/proc/stat", encoding="ascii") as stat_file:
                fields = stat_file.readline().split()[1:]
            counters = [int(value) for value in fields]
            idle = counters[3] + (counters[4] if len(counters) > 4 else 0)
            sample = (sum(counters[:8]), idle)
        else:
            raise OSError("CPU usage is not available on this platform")

        previous = self.previous
        self.previous = sample
        if previous is None:
            return 0.0
        total_delta = sample[0] - previous[0]
        idle_delta = sample[1] - previous[1]
        if total_delta <= 0:
            return 0.0
        return max(0.0, min(100.0, 100 * (total_delta - idle_delta) / total_delta))


def uptime_seconds() -> float:
    if sys.platform == "win32":
        return ctypes.windll.kernel32.GetTickCount64() / 1000
    if os.path.exists("/proc/uptime"):
        with open("/proc/uptime", encoding="ascii") as uptime_file:
            return float(uptime_file.readline().split()[0])
    raise OSError("Uptime is not available on this platform")


def format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{amount:.0f} B"
        amount /= 1024
    return f"{amount:.1f} TB"


def format_uptime(seconds: float) -> str:
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    return f"{hours}h {minutes}m"


class PcDashboard:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cpu = CpuSampler()
        self.refresh_job: str | None = None
        root.title("PC Dashboard")
        root.geometry("760x570")
        root.minsize(650, 520)
        root.configure(bg=BG)

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TProgressbar", troughcolor="#293447", background=ACCENT, bordercolor="#293447")
        style.configure("Blue.Horizontal.TProgressbar", troughcolor="#293447", background=BLUE, bordercolor="#293447")

        outer = tk.Frame(root, bg=BG, padx=28, pady=24)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x", pady=(0, 22))
        tk.Label(header, text="Your PC, at a glance", bg=BG, fg=TEXT,
                 font=("Segoe UI", 22, "bold")).pack(anchor="w")
        self.subtitle = tk.Label(header, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.subtitle.pack(anchor="w", pady=(5, 0))

        self.metric_values: dict[str, tk.Label] = {}
        self.metric_bars: dict[str, ttk.Progressbar] = {}
        cards = tk.Frame(outer, bg=BG)
        cards.pack(fill="both", expand=True)
        cards.columnconfigure((0, 1), weight=1, uniform="cards")
        cards.rowconfigure((0, 1), weight=1, uniform="cards")

        self._metric_card(cards, "CPU", "cpu", 0, 0, BLUE)
        self._metric_card(cards, "Memory", "memory", 0, 1, ACCENT)
        self._metric_card(cards, "System drive", "disk", 1, 0, "#c49aff")
        self._info_card(cards, "System", 1, 1)

        footer = tk.Frame(outer, bg=BG)
        footer.pack(fill="x", pady=(18, 0))
        self.status = tk.Label(footer, text="Starting up…", bg=BG, fg=MUTED,
                               font=("Segoe UI", 9))
        self.status.pack(side="left")
        tk.Button(footer, text="Refresh now", command=self.refresh, bg="#26354a", fg=TEXT,
                  activebackground="#344862", activeforeground=TEXT, relief="flat",
                  padx=14, pady=7, cursor="hand2", font=("Segoe UI", 9, "bold")).pack(side="right")

        self.refresh()

    def _metric_card(self, parent: tk.Frame, title: str, key: str, row: int, column: int,
                     color: str) -> None:
        card = tk.Frame(parent, bg=PANEL, padx=20, pady=18)
        card.grid(row=row, column=column, sticky="nsew", padx=7, pady=7)
        tk.Label(card, text=title.upper(), bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        value = tk.Label(card, text="—", bg=PANEL, fg=TEXT, font=("Segoe UI", 25, "bold"))
        value.pack(anchor="w", pady=(13, 3))
        detail = tk.Label(card, text="Waiting for reading", bg=PANEL, fg=MUTED,
                          font=("Segoe UI", 9))
        detail.pack(anchor="w", pady=(0, 14))
        bar = ttk.Progressbar(card, maximum=100, style="TProgressbar" if color == ACCENT else "Blue.Horizontal.TProgressbar")
        bar.pack(fill="x")
        if color not in (ACCENT, BLUE):
            style_name = f"{key}.Horizontal.TProgressbar"
            ttk.Style(self.root).configure(style_name, troughcolor="#293447", background=color, bordercolor="#293447")
            bar.configure(style=style_name)
        self.metric_values[key] = value
        self.metric_values[f"{key}_detail"] = detail
        self.metric_bars[key] = bar

    def _info_card(self, parent: tk.Frame, title: str, row: int, column: int) -> None:
        card = tk.Frame(parent, bg=PANEL, padx=20, pady=18)
        card.grid(row=row, column=column, sticky="nsew", padx=7, pady=7)
        tk.Label(card, text=title.upper(), bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.system_info = tk.Label(card, text="", bg=PANEL, fg=TEXT, justify="left",
                                    anchor="nw", font=("Segoe UI", 10), wraplength=260)
        self.system_info.pack(fill="both", expand=True, anchor="w", pady=(15, 0))

    def _set_metric(self, key: str, value: str, detail: str, percent: float) -> None:
        self.metric_values[key].configure(text=value)
        self.metric_values[f"{key}_detail"].configure(text=detail)
        self.metric_bars[key].configure(value=percent)

    def refresh(self) -> None:
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
            self.refresh_job = None

        try:
            cpu_percent = self.cpu.usage()
            self._set_metric("cpu", f"{cpu_percent:.0f}%",
                             f"{os.cpu_count() or '—'} logical processors", cpu_percent)
        except (OSError, ValueError) as error:
            self._set_metric("cpu", "Unavailable", str(error), 0)

        try:
            used, total = memory_stats()
            memory_percent = 100 * used / total if total else 0
            self._set_metric("memory", f"{memory_percent:.0f}%",
                             f"{format_bytes(used)} used of {format_bytes(total)}", memory_percent)
        except (OSError, KeyError, ValueError) as error:
            self._set_metric("memory", "Unavailable", str(error), 0)

        try:
            root_path = os.path.abspath(os.sep)
            disk = shutil.disk_usage(root_path)
            disk_percent = 100 * disk.used / disk.total if disk.total else 0
            self._set_metric("disk", f"{disk_percent:.0f}%",
                             f"{format_bytes(disk.free)} free of {format_bytes(disk.total)}",
                             disk_percent)
        except OSError as error:
            self._set_metric("disk", "Unavailable", str(error), 0)

        try:
            uptime = format_uptime(uptime_seconds())
        except (OSError, ValueError) as error:
            uptime = f"Unavailable ({error})"
        processor = platform.processor() or "Processor details unavailable"
        self.system_info.configure(
            text=f"{platform.system()} {platform.release()}\n\n"
                 f"{socket.gethostname()}\n\n"
                 f"{processor}\n\n"
                 f"Uptime: {uptime}"
        )
        self.subtitle.configure(text="Live system stats  |  Updates every 2 seconds")
        self.status.configure(text=f"Last updated {time.strftime('%H:%M:%S')}")
        self.refresh_job = self.root.after(2000, self._scheduled_refresh)

    def _scheduled_refresh(self) -> None:
        self.refresh_job = None
        self.refresh()


def main() -> None:
    root = tk.Tk()
    PcDashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()


print("thanks for using guys!!!!")