"""How busy is the Pi? CPU / temperature readout for the FrED screen.

Shown in the "Pi load (CPU)" box, sent to the hub Pi 5 with every status ping
(laptop_link.py), printed to the terminal once a minute, and averaged over
each experiment's recording (sent to the laptop with the data, "Run info").

All numbers come from Linux's /proc and /sys files (cheap to read; nothing is
installed). Off the Pi (Windows) the readout just says it is unavailable.

How to read it
--------------
* **Pi CPU** - the whole Raspberry Pi, all 4 cores together (100% = every
  core busy). It includes everything: main.py, the desktop, the WiFi /
  hotspot (NetworkManager, the WiFi driver), ...
* **main.py** - this program, in "% of ONE core" like the `top` command.
  Python runs its code on one core at a time (the GIL), so ~100% means
  main.py is saturated even though the Pi as a whole still has room.
* **rest of the system** - Pi CPU minus main.py: where the hotspot's cost
  shows up. Compare it with FrED as the hotspot vs FrED on the hub.
* **threads** - which part of main.py uses the time: "GUI + graphs" (the
  window and the plot redraws), "hardware loop" (sensors, PID, motors),
  "network" (the laptop link), "other".
* **temperature / MHz / power** - the Pi 4 slows itself down when it is
  hot (80 C+) or its power supply is weak (under-voltage); then everything
  is slower whatever the software does.
"""
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from typing import Dict, Optional, Tuple


def throttle_text(flags: int) -> Tuple[str, bool]:
    """Decode ``vcgencmd get_throttled`` into (text, is_bad_now)."""
    if flags & 0x1:
        return "UNDER-VOLTAGE NOW (weak power supply!)", True
    if flags & 0x4:
        return "THROTTLED NOW", True
    if flags & 0x2:
        return "CPU frequency capped now", True
    if flags & 0x8:
        return "temperature limit active", True
    if flags & 0x10000:
        return "power OK now (under-voltage happened since boot)", False
    if flags & 0xE0000:
        return "OK now (throttled earlier since boot)", False
    return "power OK", False


class LoadMonitor:
    """CPU load of the Pi and of this program, averaged over a time window.

    Call :meth:`sample` about once a second (the GUI does); read
    :attr:`latest` (a dict, replaced atomically - safe from any thread).
    """

    WINDOW_S = 10.0           # the on-screen numbers average this long
    LOG_PERIOD_S = 60.0       # a terminal line with the 60 s average
    THROTTLE_PERIOD_S = 10.0  # vcgencmd is a separate process: not too often

    # Thread names (set in main.py / laptop_link.py) -> groups shown.
    THREAD_GROUPS = (("hardware", "hardware loop"),
                     ("laptop-link", "network"),
                     ("hub-status", "network"))

    def __init__(self, root: str = "") -> None:
        self.root = root      # prefix for /proc and /sys (tests use a fake)
        self.available = os.path.exists(self._path("/proc/stat")) and \
            os.path.exists(self._path("/proc/self/stat"))
        self.ncpu = os.cpu_count() or 1
        try:
            self.clk = os.sysconf("SC_CLK_TCK")
        except (AttributeError, ValueError, OSError):
            self.clk = 100
        self._history = deque()           # counter snapshots, oldest first
        self._last_log = None             # snapshot of the last minute line
        self._throttle = ("", False)
        self._throttle_time = float("-inf")
        self._lock = threading.Lock()
        self.latest: Optional[dict] = None

    def _path(self, path: str) -> str:
        return self.root + path if self.root else path

    # ------------------------------------------------------------------ #
    # Raw counters
    # ------------------------------------------------------------------ #
    @staticmethod
    def _task_jiffies(text: str) -> int:
        """utime + stime from a /proc/<pid>/stat line (fields 14 and 15)."""
        rest = text[text.rindex(")") + 2:].split()
        return int(rest[11]) + int(rest[12])

    def _thread_names(self) -> Dict[int, str]:
        names = {threading.main_thread().native_id: "GUI + graphs"}
        for thread in threading.enumerate():
            if thread.native_id is None or thread.native_id in names:
                continue
            for prefix, group in self.THREAD_GROUPS:
                if thread.name.startswith(prefix):
                    names[thread.native_id] = group
                    break
        return names

    def read_counters(self) -> Optional[dict]:
        """One snapshot of the cumulative CPU counters (None off Linux)."""
        if not self.available:
            return None
        try:
            with open(self._path("/proc/stat"), encoding="ascii") as fh:
                fields = [int(x) for x in fh.readline().split()[1:9]]
            with open(self._path("/proc/self/stat"), encoding="ascii") as fh:
                proc = self._task_jiffies(fh.read())
            threads = {}
            task_dir = self._path("/proc/self/task")
            for tid in os.listdir(task_dir):
                try:
                    with open(os.path.join(task_dir, tid, "stat"),
                              encoding="ascii") as fh:
                        threads[int(tid)] = self._task_jiffies(fh.read())
                except (OSError, ValueError):
                    pass          # the thread ended meanwhile
        except (OSError, ValueError, IndexError):
            return None
        return {"t": time.monotonic(), "total": sum(fields),
                "idle": fields[3] + fields[4], "proc": proc,
                "threads": threads, "names": self._thread_names()}

    # ------------------------------------------------------------------ #
    # Averages between two snapshots
    # ------------------------------------------------------------------ #
    def rates(self, a: dict, b: dict) -> Optional[dict]:
        """Average load between snapshots ``a`` (older) and ``b``."""
        dt = b["t"] - a["t"]
        d_total = b["total"] - a["total"]
        if dt <= 0 or d_total <= 0:
            return None
        pi_cpu = 100.0 * (1.0 - (b["idle"] - a["idle"]) / d_total)
        main_core = 100.0 * (b["proc"] - a["proc"]) / self.clk / dt
        groups: Dict[str, float] = {}
        names = b.get("names", {})
        for tid, jiffies in b["threads"].items():
            if tid in a["threads"]:
                group = names.get(tid, "other")
                groups[group] = groups.get(group, 0.0) + max(
                    0.0, 100.0 * (jiffies - a["threads"][tid]) / self.clk / dt)
        rest = max(0.0, pi_cpu - main_core / self.ncpu)
        return {"seconds": round(dt, 1),
                "pi_cpu": round(max(0.0, min(100.0, pi_cpu)), 1),
                "main_core": round(main_core, 1),
                "main_pi": round(main_core / self.ncpu, 1),
                "rest": round(rest, 1),
                "threads": {k: round(v, 1) for k, v in groups.items()}}

    def mark(self) -> Optional[dict]:
        """The newest once-a-second snapshot, to average from later. Reads
        no files, so the hardware thread can call it (recording start)."""
        with self._lock:
            return self._history[-1] if self._history else None

    def average_since(self, mark: Optional[dict]) -> Optional[dict]:
        """Average load from ``mark`` (see :meth:`mark`) to the newest
        snapshot (+-1 s), plus the temperature / power state. Cheap enough
        for the hardware thread (recording end)."""
        if mark is None:
            return None
        with self._lock:
            now = self._history[-1] if self._history else None
        if now is None or now is mark:
            return None
        result = self.rates(mark, now)
        if result is not None:
            result.update(self._sensors(refresh_throttle=False))
            result["summary"] = self.summary_text(result)
            result["threads_text"] = self.threads_text(result)
        return result

    # ------------------------------------------------------------------ #
    # Temperature, clock speed, power (throttling)
    # ------------------------------------------------------------------ #
    def _sensors(self, refresh_throttle: bool = True) -> dict:
        out = {}
        try:
            with open(self._path("/sys/class/thermal/thermal_zone0/temp"),
                      encoding="ascii") as fh:
                out["temp_c"] = round(int(fh.read().strip()) / 1000.0, 1)
        except (OSError, ValueError):
            pass
        try:
            with open(self._path(
                    "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"),
                    encoding="ascii") as fh:
                out["mhz"] = int(fh.read().strip()) // 1000
        except (OSError, ValueError):
            pass
        now = time.monotonic()
        if (refresh_throttle and not self.root
                and now - self._throttle_time >= self.THROTTLE_PERIOD_S
                and shutil.which("vcgencmd")):
            self._throttle_time = now
            try:
                res = subprocess.run(["vcgencmd", "get_throttled"],
                                     capture_output=True, text=True, timeout=2)
                m = re.search(r"0x([0-9a-fA-F]+)", res.stdout)
                if m:
                    self._throttle = throttle_text(int(m.group(1), 16))
            except (OSError, subprocess.SubprocessError):
                pass
        if self._throttle[0]:
            out["power"], out["power_bad"] = self._throttle
        return out

    # ------------------------------------------------------------------ #
    # Periodic sampling (GUI timer, once a second)
    # ------------------------------------------------------------------ #
    def sample(self) -> Optional[dict]:
        """Take a snapshot; refresh :attr:`latest`; print the minute line."""
        snap = self.read_counters()
        if snap is None:
            return None
        with self._lock:
            self._history.append(snap)
            while (len(self._history) > 2 and
                   snap["t"] - self._history[1]["t"] >= self.WINDOW_S):
                self._history.popleft()
            oldest = self._history[0]
        result = self.rates(oldest, snap) if oldest is not snap else None
        if result is None:
            return None
        result.update(self._sensors())
        result["summary"] = self.summary_text(result)
        result["threads_text"] = self.threads_text(result)
        result["bad"] = self.is_bad(result)
        self.latest = result

        if self._last_log is None:
            self._last_log = snap
        elif snap["t"] - self._last_log["t"] >= self.LOG_PERIOD_S:
            minute = self.rates(self._last_log, snap)
            self._last_log = snap
            if minute is not None:
                minute.update(self._sensors())
                print(f"[Pi load] {minute['seconds']:.0f} s average: "
                      f"{self.summary_text(minute)} | threads: "
                      f"{self.threads_text(minute)}", flush=True)
        return result

    # ------------------------------------------------------------------ #
    # Text
    # ------------------------------------------------------------------ #
    @staticmethod
    def summary_text(r: dict) -> str:
        text = (f"Pi CPU {r['pi_cpu']:.0f}%  |  main.py {r['main_core']:.0f}% "
                f"of one core  |  rest of system {r['rest']:.0f}%")
        extra = []
        if "temp_c" in r:
            extra.append(f"{r['temp_c']:.0f} C")
        if "mhz" in r:
            extra.append(f"{r['mhz']} MHz")
        if r.get("power"):
            extra.append(r["power"])
        return text + ("  |  " + ", ".join(extra) if extra else "")

    @staticmethod
    def threads_text(r: dict) -> str:
        order = ("GUI + graphs", "hardware loop", "network", "other")
        threads = r.get("threads") or {}
        return ", ".join(f"{name} {threads[name]:.0f}%"
                         for name in order if name in threads)

    @staticmethod
    def is_bad(r: dict) -> bool:
        """main.py saturated, Pi hot, or power trouble right now."""
        return (r.get("main_core", 0) >= 85 or r.get("temp_c", 0) >= 80
                or bool(r.get("power_bad")))

    def hub_report(self) -> Optional[dict]:
        """The compact copy sent to the hub with every status ping."""
        r = self.latest
        if r is None:
            return None
        return {"summary": r["summary"], "threads": r["threads_text"],
                "bad": r["bad"], "pi_cpu": r["pi_cpu"],
                "main_core": r["main_core"], "rest": r["rest"],
                "temp_c": r.get("temp_c")}
