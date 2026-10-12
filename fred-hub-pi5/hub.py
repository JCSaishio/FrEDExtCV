#!/usr/bin/env python3
"""FrED hub (Raspberry Pi 5): WiFi hotspot dashboard + optional relay.

The hub Pi 5 runs the FrED_Hub WiFi (hub_hotspot.sh). FrED's Pi 4 joins it at
the fixed address FRED_IP (``bash setup_wifi.sh hub`` on FrED) and the laptop
joins it by DHCP. The laptop then reaches FrED in one of two ways, chosen in
the laptop app's "Network" box:

  Hub - direct   laptop -> FRED_IP:5005. The hub only carries the WiFi
                 packets, like a router; this program just watches.
  Hub - relay    laptop -> HUB_IP:5005 -> this program -> FRED_IP:5005.
                 For every laptop connection the relay opens its own
                 connection to FrED and copies the bytes both ways,
                 unchanged, so experiments, data retrieval and the clock sync
                 work exactly as in direct mode (the sync simply measures the
                 longer path).

The dashboard (refreshed every second) shows:
  * the hotspot (SSID, channel) and the hub's own CPU / temperature,
  * every device on the WiFi: address, name, signal strength, link speed,
  * ping round-trip times to FrED and to the laptop (every 2 s),
  * FrED's answer to a status ping (UDP STATUS_PORT): whether main.py runs,
    whether a laptop is connected to it (directly or through the relay), the
    experiment phase and FrED's CPU load,
  * the relay: who is connected, messages per type in each direction, and the
    hub <-> FrED round trip of the laptop's clock-sync pings,
  * a log of events (device joined/left, FrED (un)reachable, relay ...).

Run:  bash start_hub.sh  [--plain] [--no-relay] [--sim]
      python3 hub.py --help        for every option
Keys: q = quit, r = reset the ping / relay statistics.

Only Python's standard library is used. Settings come from hub.conf.
"""
import argparse
import json
import os
import random
import re
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import textwrap
import threading
import time
from collections import Counter, deque
from typing import Dict, List, Optional, Tuple

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
CONF_FILE = os.path.join(HUB_DIR, "hub.conf")

# Used when hub.conf is missing a key. hub.conf is the place to change them.
DEFAULT_CONF = {
    "HUB_SSID": "FrED_Hub",
    "HUB_PASSWORD": "fredfiber123",
    "HUB_IP": "192.168.50.1",
    "FRED_IP": "192.168.50.2",
    "LINK_PORT": "5005",
    "STATUS_PORT": "5006",
    "WIFI_COUNTRY": "MX",
    "BAND": "bg",
    "CHANNEL": "",
    "IFACE": "wlan0",
    "CON_NAME": "FrED_Hub_AP",
}

PING_PERIOD_S = 2.0       # ping FrED and the laptop this often
STATUS_PERIOD_S = 2.0     # status-ping FrED's program this often
NET_PERIOD_S = 2.0        # re-read the WiFi station list this often
FRED_LOST_PINGS = 3       # consecutive lost pings before "FrED unreachable"
STATUS_STALE_S = 6.0      # no status answer for this long -> "not answering"
RELAY_IDLE_S = 20.0       # the laptop pings every 2 s: a relay session that
                          # hears nothing from it for this long is dead
                          # (WiFi dropped) and is closed, freeing FrED's slot
RELAY_SOCK_TIMEOUT_S = 30.0   # one send may take this long (big data reply)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
_CONF_LINE = re.compile(
    r"""^\s*([A-Za-z_][A-Za-z0-9_]*)=(?:"([^"]*)"|'([^']*)'|([^\s#]*))""")


def load_conf(path: str = CONF_FILE) -> Dict[str, str]:
    """Read the KEY="value" lines of hub.conf (the bash scripts source it)."""
    conf = dict(DEFAULT_CONF)
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = _CONF_LINE.match(line)
                if m:
                    value = next(v for v in m.groups()[1:] if v is not None)
                    conf[m.group(1)] = value
    except OSError:
        pass
    return conf


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def run_cmd(args: List[str], timeout: float = 3.0) -> Optional[str]:
    """Run a command and return its stdout, or None if it failed."""
    try:
        res = subprocess.run(args, capture_output=True, text=True,
                             timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return res.stdout


def fmt_ms(value: Optional[float]) -> str:
    if value is None:
        return "--"
    return f"{value:.1f}" if value < 100 else f"{value:.0f}"


def fmt_bytes(n: float) -> str:
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000.0
    return f"{n:.1f} GB"


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


class RttStats:
    """Round-trip times (ms) of the last ``n`` tries; None = lost."""

    def __init__(self, n: int = 30) -> None:
        self._samples = deque(maxlen=n)
        self._lock = threading.Lock()
        self.lost_in_a_row = 0

    def add(self, value: Optional[float]) -> None:
        with self._lock:
            self._samples.append(value)
            self.lost_in_a_row = self.lost_in_a_row + 1 if value is None else 0

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self.lost_in_a_row = 0

    def summary(self) -> dict:
        with self._lock:
            samples = list(self._samples)
        ok = [s for s in samples if s is not None]
        return {
            "count": len(samples),
            "last": samples[-1] if samples else None,
            "min": min(ok) if ok else None,
            "median": statistics.median(ok) if ok else None,
            "max": max(ok) if ok else None,
            "loss": (100.0 * (len(samples) - len(ok)) / len(samples)
                     if samples else None),
        }

    @staticmethod
    def text(s: dict) -> str:
        if not s["count"]:
            return "waiting..."
        loss = f"{s['loss']:.0f}%" if s["loss"] is not None else "--"
        return (f"{fmt_ms(s['last'])} ms now, median {fmt_ms(s['median'])}, "
                f"max {fmt_ms(s['max'])}, lost {loss}")


class EventLog:
    """Time-stamped event lines (also printed at once in --plain mode)."""

    def __init__(self, echo: bool = False) -> None:
        self._lines = deque(maxlen=200)
        self._lock = threading.Lock()
        self.echo = echo

    def add(self, text: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}  {text}"
        with self._lock:
            self._lines.append(line)
        if self.echo:
            print(line, flush=True)

    def recent(self, n: int) -> List[str]:
        with self._lock:
            return list(self._lines)[-n:] if n > 0 else []


# --------------------------------------------------------------------------- #
# Pinging FrED and the laptop (ICMP, through the system `ping`)
# --------------------------------------------------------------------------- #
class Pinger:
    """Ping every target once per PING_PERIOD_S (one thread for all)."""

    _TIME = re.compile(r"time[=<]\s*([\d.]+)\s*ms")

    def __init__(self, sim: bool = False) -> None:
        self.sim = sim
        self._targets: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="pinger",
                                        daemon=True)
        self._thread.start()

    def set_target(self, name: str, ip: Optional[str]) -> None:
        with self._lock:
            target = self._targets.get(name)
            if target is None or target["ip"] != ip:
                self._targets[name] = {"ip": ip, "stats": RttStats()}

    def stats(self, name: str) -> Tuple[Optional[str], Optional[RttStats]]:
        with self._lock:
            target = self._targets.get(name)
        return (target["ip"], target["stats"]) if target else (None, None)

    def reset(self) -> None:
        with self._lock:
            for target in self._targets.values():
                target["stats"].reset()

    def _ping(self, ip: str) -> Optional[float]:
        if self.sim:
            time.sleep(0.01)
            return None if random.random() < 0.02 else abs(random.gauss(3, 1.5))
        out = run_cmd(["ping", "-n", "-c", "1", "-W", "1", ip], timeout=3.0)
        if out is None:
            return None
        m = self._TIME.search(out)
        return float(m.group(1)) if m else None

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                targets = list(self._targets.values())
            for target in targets:
                if target["ip"]:
                    target["stats"].add(self._ping(target["ip"]))
            self._stop.wait(PING_PERIOD_S)

    def close(self) -> None:
        self._stop.set()


# --------------------------------------------------------------------------- #
# FrED's status ping (UDP, answered by laptop_link.py on FrED)
# --------------------------------------------------------------------------- #
class FredStatus:
    """Ask FrED's main.py how it is doing, every STATUS_PERIOD_S.

    FrED answers {"type": "hub_pong", ...} with: program/version, whether a
    laptop is connected (and from where), the experiment status and its CPU
    load. This is a separate UDP port, so it never disturbs the laptop's TCP
    connection (FrED serves one laptop at a time).
    """

    def __init__(self, ip: str, port: int) -> None:
        self.ip = ip
        self.port = port
        self.reply: Optional[dict] = None
        self.reply_time = float("-inf")    # time.monotonic() of last answer
        self.rtt = RttStats()
        self._id = 0
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(1.0)
        self._thread = threading.Thread(target=self._loop, name="fred-status",
                                        daemon=True)
        self._thread.start()

    @property
    def answering(self) -> bool:
        return time.monotonic() - self.reply_time < STATUS_STALE_S

    def query_now(self, timeout: float = 0.5) -> Optional[dict]:
        """One immediate status ping on its own socket, for decisions that
        must not rely on an answer up to STATUS_PERIOD_S old. None if FrED
        does not answer in time."""
        query_id = random.randint(1 << 20, 1 << 30)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout)
                sock.sendto(json.dumps({"type": "hub_ping",
                                        "id": query_id}).encode(),
                            (self.ip, self.port))
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    data, _ = sock.recvfrom(65536)
                    msg = json.loads(data.decode("utf-8", "ignore"))
                    if (isinstance(msg, dict) and msg.get("type") == "hub_pong"
                            and msg.get("id") == query_id):
                        return msg
        except (OSError, ValueError):
            pass            # timeout / no route / garbage: no answer
        return None

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._id += 1
            sent = time.monotonic()
            got = None
            try:
                self._sock.sendto(json.dumps(
                    {"type": "hub_ping", "id": self._id}).encode(),
                    (self.ip, self.port))
                deadline = sent + 1.0
                while got is None and time.monotonic() < deadline:
                    try:
                        data, _ = self._sock.recvfrom(65536)
                    except socket.timeout:
                        break
                    try:
                        msg = json.loads(data.decode("utf-8", "ignore"))
                    except ValueError:
                        continue
                    if (isinstance(msg, dict) and msg.get("type") == "hub_pong"
                            and msg.get("id") == self._id):
                        got = msg
            except OSError:
                pass       # no route yet (FrED not on the WiFi)
            if got is not None:
                now = time.monotonic()
                self.reply, self.reply_time = got, now
                self.rtt.add((now - sent) * 1000.0)
            else:
                self.rtt.add(None)
            self._stop.wait(max(0.0, STATUS_PERIOD_S - (time.monotonic() - sent)))

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# WiFi state: hotspot, connected stations, the hub's own health
# --------------------------------------------------------------------------- #
class NetWatch:
    """Poll the hotspot and its connected devices every NET_PERIOD_S."""

    def __init__(self, conf: Dict[str, str], log: EventLog,
                 sim: bool = False, fred_ip: Optional[str] = None) -> None:
        self.conf = conf
        self.iface = conf["IFACE"]
        self.fred_ip = fred_ip or conf["FRED_IP"]
        self.log = log
        self.sim = sim
        self.hotspot_up: Optional[bool] = None
        self.iface_info = ""          # e.g. "channel 6 (2437 MHz)"
        self.stations: List[dict] = []
        self.hub_health = ""
        self.hub_health_bad = False
        self.iw_error = ""
        self._cpu_prev = None
        self._throttle = ("", False)
        self._throttle_time = float("-inf")
        self._known: Dict[str, str] = {}   # mac -> label, for join/leave log
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="netwatch",
                                        daemon=True)
        self._thread.start()

    # ---- parsing --------------------------------------------------------- #
    @staticmethod
    def parse_station_dump(text: str) -> Dict[str, dict]:
        stations: Dict[str, dict] = {}
        current = None
        for raw in text.splitlines():
            line = raw.strip()
            m = re.match(r"Station ([0-9a-fA-F:]{17})", line)
            if m:
                current = {"mac": m.group(1).lower()}
                stations[current["mac"]] = current
                continue
            if current is None:
                continue
            for key, pattern, cast in (
                    ("signal", r"^signal:\s*(-?\d+)", int),
                    ("tx_mbit", r"^tx bitrate:\s*([\d.]+)", float),
                    ("rx_mbit", r"^rx bitrate:\s*([\d.]+)", float),
                    ("connected_s", r"^connected time:\s*(\d+)", int),
                    ("inactive_ms", r"^inactive time:\s*(\d+)", int),
                    ("tx_retries", r"^tx retries:\s*(\d+)", int),
                    ("tx_failed", r"^tx failed:\s*(\d+)", int)):
                m = re.match(pattern, line)
                if m:
                    current[key] = cast(m.group(1))
        return stations

    @staticmethod
    def parse_neighbors(text: str) -> Dict[str, str]:
        """``ip -4 neigh`` output -> {mac: ip}."""
        result = {}
        for line in text.splitlines():
            parts = line.split()
            if "lladdr" in parts:
                mac = parts[parts.index("lladdr") + 1].lower()
                result[mac] = parts[0]
        return result

    @staticmethod
    def parse_leases(text: str) -> Dict[str, Tuple[str, str]]:
        """dnsmasq leases -> {mac: (ip, hostname)}."""
        result = {}
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                name = "" if parts[3] == "*" else parts[3]
                result[parts[1].lower()] = (parts[2], name)
        return result

    # ---- reading --------------------------------------------------------- #
    def _read_real(self) -> None:
        dump = run_cmd(["iw", "dev", self.iface, "station", "dump"])
        if dump is None and shutil.which("sudo"):
            dump = run_cmd(["sudo", "-n", "iw", "dev", self.iface,
                            "station", "dump"])
        self.iw_error = "" if dump is not None else (
            "cannot read the WiFi station list (iw) - signal unknown")
        stations = self.parse_station_dump(dump or "")
        neigh = self.parse_neighbors(
            run_cmd(["ip", "-4", "neigh", "show", "dev", self.iface]) or "")
        leases = {}
        try:
            with open(f"/var/lib/NetworkManager/dnsmasq-{self.iface}.leases",
                      encoding="utf-8") as fh:
                leases = self.parse_leases(fh.read())
        except OSError:
            pass
        for mac, st in stations.items():
            ip, name = leases.get(mac, (None, ""))
            st["ip"] = neigh.get(mac) or ip
            st["name"] = name
        # Devices answering ARP but missing from the station dump (iw not
        # readable): still list them, without radio details.
        if dump is None:
            for mac, ip in neigh.items():
                stations.setdefault(mac, {"mac": mac, "ip": ip,
                                          "name": leases.get(mac, ("", ""))[1]})
        self.stations = sorted(stations.values(),
                               key=lambda s: (s.get("ip") or "~", s["mac"]))

        info = run_cmd(["iw", "dev", self.iface, "info"]) or ""
        mode = re.search(r"type\s+(\S+)", info)
        chan = re.search(r"channel\s+(\d+\s*\([\d.]+ MHz\))", info)
        addr = run_cmd(["ip", "-4", "addr", "show", "dev", self.iface]) or ""
        has_ip = f"inet {self.conf['HUB_IP']}/" in addr
        is_ap = bool(mode and mode.group(1).upper() == "AP")
        self._set_hotspot(has_ip and (is_ap or not info))
        self.iface_info = chan.group(1) if chan else ""
        self.hub_health, self.hub_health_bad = self._health()

    def _read_sim(self) -> None:
        self._set_hotspot(True)
        self.iface_info = "6 (2437 MHz)"
        prev = {s["mac"]: s for s in self.stations}

        def station(mac, ip, name):
            old = prev.get(mac, {})
            return {"mac": mac, "ip": ip, "name": name,
                    "signal": int(old.get("signal", -48)
                                  + random.choice((-1, 0, 0, 1))),
                    "tx_mbit": 65.0, "rx_mbit": 58.5,
                    "connected_s": old.get("connected_s", 0) + 2,
                    "inactive_ms": random.randint(0, 900)}
        self.stations = [
            station("dc:a6:32:00:00:02", self.fred_ip, ""),
            station("3c:91:80:00:00:17", "192.168.50.17", "LAPTOP-SIM"),
        ]
        self.hub_health, self.hub_health_bad = "sim mode (no real WiFi)", False

    def _set_hotspot(self, up: bool) -> None:
        if up != self.hotspot_up:
            if self.hotspot_up is not None or not up:
                self.log.add(f"hotspot {'UP' if up else 'DOWN'} on {self.iface}"
                             + ("" if up else
                                " - run: bash hub_hotspot.sh"))
            self.hotspot_up = up

    def _health(self) -> Tuple[str, bool]:
        """The hub Pi 5's own CPU %, temperature and power state."""
        parts, bad = [], False
        try:
            with open("/proc/stat", encoding="ascii") as fh:
                fields = [int(x) for x in fh.readline().split()[1:9]]
            total, idle = sum(fields), fields[3] + fields[4]
            if self._cpu_prev is not None:
                d_total = total - self._cpu_prev[0]
                d_idle = idle - self._cpu_prev[1]
                if d_total > 0:
                    parts.append(f"CPU {100.0 * (1 - d_idle / d_total):.0f}%")
            self._cpu_prev = (total, idle)
        except (OSError, ValueError, IndexError):
            pass
        try:
            with open("/sys/class/thermal/thermal_zone0/temp",
                      encoding="ascii") as fh:
                temp = int(fh.read().strip()) / 1000.0
            parts.append(f"{temp:.0f} C")
            bad = bad or temp >= 80
        except (OSError, ValueError):
            pass
        now = time.monotonic()
        if now - self._throttle_time >= 10 and shutil.which("vcgencmd"):
            self._throttle_time = now
            out = run_cmd(["vcgencmd", "get_throttled"]) or ""
            m = re.search(r"0x([0-9a-fA-F]+)", out)
            if m:
                self._throttle = throttle_text(int(m.group(1), 16))
        if self._throttle[0]:
            parts.append(self._throttle[0])
            bad = bad or self._throttle[1]
        return "  ".join(parts), bad

    def _log_joins(self) -> None:
        now = {}
        for st in self.stations:
            label = st.get("ip") or st["mac"]
            if st.get("ip") == self.fred_ip:
                label = f"FrED ({label})"
            elif st.get("name"):
                label = f"{st['name']} ({label})"
            now[st["mac"]] = label
        for mac, label in now.items():
            old = self._known.get(mac)
            if old is None:
                self.log.add(f"joined the WiFi: {label}")
            elif old != label and "(" in label and "(" not in old:
                self.log.add(f"identified: {label}")
        for mac, label in self._known.items():
            if mac not in now:
                self.log.add(f"left the WiFi: {label}")
        self._known = now

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.sim:
                    self._read_sim()
                else:
                    self._read_real()
                self._log_joins()
            except Exception as exc:          # never kill the dashboard
                self.log.add(f"network read error: {exc}")
            self._stop.wait(NET_PERIOD_S)

    def close(self) -> None:
        self._stop.set()


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


# --------------------------------------------------------------------------- #
# The relay
# --------------------------------------------------------------------------- #
_TYPE_RE = re.compile(rb'"type"\s*:\s*"([A-Za-z_]+)"')
_ID_RE = re.compile(rb'"id"\s*:\s*(-?\d+)')
_PHASE_RE = re.compile(rb'"phase"\s*:\s*"([A-Za-z_]+)"')
_MSG_RE = re.compile(rb'"message"\s*:\s*"([^"\\]{0,80})')


class LineScanner:
    """Look at the start of every newline-delimited message going through.

    Only the first PREFIX bytes of each line are kept (enough for its
    "type", "id" and "phase"), so a >1 MB data line costs no memory. The
    bytes themselves are forwarded untouched before they get here.
    """

    PREFIX = 240

    def __init__(self, on_line) -> None:
        self._prefix = bytearray()
        self._on_line = on_line

    def feed(self, data: bytes, t: float) -> None:
        start = 0
        while True:
            newline = data.find(b"\n", start)
            end = len(data) if newline == -1 else newline
            room = self.PREFIX - len(self._prefix)
            if room > 0:
                self._prefix += data[start:min(end, start + room)]
            if newline == -1:
                return
            self._on_line(bytes(self._prefix), t)
            self._prefix.clear()
            start = newline + 1


class RelaySession:
    """One laptop <-> FrED connection pair, bytes copied both ways."""

    def __init__(self, laptop: socket.socket, laptop_addr, fred: socket.socket,
                 log: EventLog) -> None:
        self.laptop = laptop
        self.laptop_ip = laptop_addr[0]
        self.fred = fred
        self.log = log
        self.started = time.time()
        self.alive = True
        self.close_reason = ""
        self.counts = {"up": Counter(), "down": Counter()}   # up = to FrED
        self.bytes = {"up": 0, "down": 0}
        self.last_rx = {"up": time.monotonic(), "down": time.monotonic()}
        self.sync_rtt = RttStats()         # hub <-> FrED leg of sync pings
        self.fred_phase = ""
        self.fred_message = ""
        self._sync_sent: Dict[int, float] = {}
        self._lock = threading.Lock()
        for sock in (laptop, fred):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(RELAY_SOCK_TIMEOUT_S)
        threading.Thread(target=self._pipe, args=(laptop, fred, "up"),
                         name="relay-up", daemon=True).start()
        threading.Thread(target=self._pipe, args=(fred, laptop, "down"),
                         name="relay-down", daemon=True).start()

    def idle_s(self) -> float:
        """Seconds since the laptop last sent anything."""
        return time.monotonic() - self.last_rx["up"]

    def _pipe(self, src: socket.socket, dst: socket.socket,
              direction: str) -> None:
        scanner = LineScanner(
            lambda prefix, t: self._on_line(direction, prefix, t))
        who = "laptop" if direction == "up" else "FrED"
        reason = f"{who} closed the connection"
        try:
            while self.alive:
                try:
                    data = src.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    break
                t = time.monotonic()
                dst.sendall(data)
                with self._lock:
                    self.bytes[direction] += len(data)
                    self.last_rx[direction] = t
                scanner.feed(data, t)
        except OSError as exc:
            reason = f"{who} link error ({exc})"
        self.close(reason)

    def _on_line(self, direction: str, prefix: bytes, t: float) -> None:
        m = _TYPE_RE.search(prefix)
        mtype = m.group(1).decode() if m else "other"
        with self._lock:
            self.counts[direction][mtype] += 1
            if direction == "up" and mtype == "sync":
                mid = _ID_RE.search(prefix)
                if mid:
                    self._sync_sent[int(mid.group(1))] = t
                    if len(self._sync_sent) > 100:
                        self._sync_sent.pop(next(iter(self._sync_sent)))
            elif direction == "down" and mtype == "sync_reply":
                mid = _ID_RE.search(prefix)
                sent = self._sync_sent.pop(int(mid.group(1)), None) if mid else None
                if sent is not None:
                    self.sync_rtt.add((t - sent) * 1000.0)
            elif direction == "down" and mtype == "status":
                phase = _PHASE_RE.search(prefix)
                text = _MSG_RE.search(prefix)
                self.fred_phase = phase.group(1).decode() if phase else ""
                self.fred_message = (text.group(1).decode("utf-8", "ignore")
                                     if text else "")

    def close(self, reason: str = "closed") -> None:
        with self._lock:
            if not self.alive:
                return
            self.alive = False
            self.close_reason = reason
        for sock in (self.laptop, self.fred):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self.log.add(f"relay: session with {self.laptop_ip} ended - {reason}")

    def snapshot(self) -> dict:
        with self._lock:
            return {"up": dict(self.counts["up"]),
                    "down": dict(self.counts["down"]),
                    "bytes": dict(self.bytes)}


class Relay:
    """Accept laptops on HUB_IP:LINK_PORT and connect each one to FrED."""

    CONNECT_TIMEOUT_S = 3.0

    def __init__(self, listen_port: int, fred_ip: str, fred_port: int,
                 log: EventLog, fred_status: FredStatus, hub_ip: str) -> None:
        self.listen_port = listen_port
        self.fred_ip = fred_ip
        self.fred_port = fred_port
        self.hub_ip = hub_ip
        self.log = log
        self.fred_status = fred_status
        self.session: Optional[RelaySession] = None
        self.last_session: Optional[RelaySession] = None
        self.listening = False
        self.error = ""
        self._stop = threading.Event()
        self._server: Optional[socket.socket] = None
        self._thread = threading.Thread(target=self._accept_loop,
                                        name="relay-accept", daemon=True)
        self._thread.start()

    def _open(self) -> bool:
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", self.listen_port))
            srv.listen(4)
            srv.settimeout(1.0)
        except OSError as exc:
            if not self.error:
                self.log.add(f"relay: cannot listen on port {self.listen_port}"
                             f" ({exc}) - is another hub.py running?")
            self.error = str(exc)
            return False
        self._server, self.listening, self.error = srv, True, ""
        self.log.add(f"relay: listening on port {self.listen_port} "
                     f"-> FrED {self.fred_ip}:{self.fred_port}")
        return True

    def _accept_loop(self) -> None:
        while not self._stop.is_set() and not self._open():
            self._stop.wait(3.0)
        while not self._stop.is_set():
            session = self.session
            if session is not None and session.alive \
                    and session.idle_s() > RELAY_IDLE_S:
                session.close(f"laptop silent for {RELAY_IDLE_S:.0f} s "
                              "(WiFi dropped?)")
            try:
                client, addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                time.sleep(0.5)
                continue
            threading.Thread(target=self._start_session, args=(client, addr),
                             name="relay-open", daemon=True).start()

    @staticmethod
    def _reject(client: socket.socket, event: str, text: str) -> None:
        """Tell the laptop app why (it shows the message), then hang up."""
        try:
            client.settimeout(2.0)
            client.sendall((json.dumps({"type": "event", "event": event,
                                        "message": text}) + "\n").encode())
        except OSError:
            pass
        try:
            client.close()
        except OSError:
            pass

    def _start_session(self, client: socket.socket, addr) -> None:
        current = self.session
        if current is not None and current.alive:
            self.log.add(f"relay: refused {addr[0]} - {current.laptop_ip} "
                         "is already connected through the relay")
            self._reject(client, "relay_busy",
                         f"Another laptop ({current.laptop_ip}) is already "
                         "connected to FrED through the hub relay.")
            return
        # FrED serves one laptop at a time: if ANOTHER laptop is connected to
        # it directly, a relayed connection would just wait in its queue.
        # Ask FrED now (the periodic answer can be 2 s old); the same laptop
        # switching from direct to relay has closed its direct link already.
        query = getattr(self.fred_status, "query_now", None)
        reply = query() if query else None
        direct = reply.get("laptop") if reply else None
        if direct and not reply.get("laptop_via_hub") \
                and str(direct).rsplit(":", 1)[0] != addr[0]:
            self.log.add(f"relay: refused {addr[0]} - FrED already has a "
                         f"laptop connected directly ({direct})")
            self._reject(client, "relay_busy",
                         f"FrED already has a laptop connected directly "
                         f"({direct}). Disconnect it first, then use the relay.")
            return
        try:
            fred = socket.create_connection((self.fred_ip, self.fred_port),
                                            timeout=self.CONNECT_TIMEOUT_S)
        except OSError as exc:
            self.log.add(f"relay: {addr[0]} connected but FrED "
                         f"{self.fred_ip}:{self.fred_port} is unreachable "
                         f"({exc})")
            self._reject(client, "relay_error",
                         f"The hub relay could not reach FrED at "
                         f"{self.fred_ip}:{self.fred_port} ({exc}).\n\n"
                         "Is FrED on the hub WiFi (bash setup_wifi.sh hub on "
                         "FrED) and is its program (start_fred.sh) running?")
            return
        self.log.add(f"relay: {addr[0]} <-> FrED {self.fred_ip}:"
                     f"{self.fred_port} connected")
        session = RelaySession(client, addr, fred, self.log)
        self.session = self.last_session = session

    def close(self) -> None:
        self._stop.set()
        if self.session is not None:
            self.session.close("hub stopped")
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# The hub: ties everything together and builds the screen
# --------------------------------------------------------------------------- #
class Hub:
    def __init__(self, conf: Dict[str, str], args) -> None:
        self.conf = conf
        self.args = args
        self.sim = args.sim
        self.started = time.time()
        self.fred_ip = args.fred_ip or ("127.0.0.1" if args.sim
                                        else conf["FRED_IP"])
        self.fred_port = args.fred_port or int(conf["LINK_PORT"])
        self.status_port = args.status_port or int(conf["STATUS_PORT"])
        self.relay_port = args.relay_port or (int(conf["LINK_PORT"]) + 10
                                              if args.sim
                                              else int(conf["LINK_PORT"]))
        self.log = EventLog(echo=args.plain)
        self.log.add("hub started" + (" (SIM MODE: fake WiFi devices)"
                                      if args.sim else ""))
        self.net = NetWatch(conf, self.log, sim=args.sim, fred_ip=self.fred_ip)
        self.pinger = Pinger(sim=args.sim)
        self.pinger.set_target("fred", self.fred_ip)
        self.fred = FredStatus(self.fred_ip, self.status_port)
        self.relay = (None if args.no_relay else
                      Relay(self.relay_port, self.fred_ip, self.fred_port,
                            self.log, self.fred, conf["HUB_IP"]))
        # Previous states, to log only the changes.
        self._fred_reachable: Optional[bool] = None
        self._fred_answering: Optional[bool] = None
        self._fred_laptop: Optional[str] = "?"

    # ---- state -------------------------------------------------------- #
    def laptop_ip(self) -> Optional[str]:
        """Best guess at the laptop's address (to ping it)."""
        relay = self.relay.session if self.relay else None
        if relay is not None and relay.alive:
            return relay.laptop_ip
        reply = self.fred.reply if self.fred.answering else None
        if reply and reply.get("laptop") and not reply.get("laptop_via_hub"):
            return str(reply["laptop"]).rsplit(":", 1)[0]
        others = [s for s in self.net.stations
                  if s.get("ip") and s["ip"] != self.fred_ip]
        if len(others) == 1:
            return others[0]["ip"]
        previous, _ = self.pinger.stats("laptop")
        if previous and any(s.get("ip") == previous for s in others):
            return previous
        return None

    def tick(self) -> None:
        """Once a second: update the ping targets and log state changes."""
        laptop = self.laptop_ip()
        if laptop:
            self.pinger.set_target("laptop", laptop)

        _, fred_stats = self.pinger.stats("fred")
        if fred_stats is not None and fred_stats.summary()["count"]:
            reachable = fred_stats.lost_in_a_row < FRED_LOST_PINGS
            if reachable != self._fred_reachable:
                if reachable:
                    self.log.add(f"FrED answers ping ({self.fred_ip})")
                else:
                    self.log.add(f"FrED does NOT answer ping ({self.fred_ip})"
                                 " - is it on the hub WiFi? (on FrED: bash "
                                 "setup_wifi.sh hub)")
                self._fred_reachable = reachable

        answering = self.fred.answering
        if answering != self._fred_answering:
            if self._fred_answering is not None or answering:
                self.log.add("FrED program (main.py) is answering"
                             if answering else
                             "FrED program not answering (main.py stopped?)")
            self._fred_answering = answering

        reply = self.fred.reply if answering else None
        if reply is not None:
            laptop_now = reply.get("laptop") or ""
            if laptop_now != self._fred_laptop:
                if laptop_now:
                    how = ("through the hub relay"
                           if reply.get("laptop_via_hub") else
                           f"DIRECTLY from {laptop_now}")
                    self.log.add(f"FrED: laptop connected {how}")
                elif self._fred_laptop not in ("?", ""):
                    self.log.add("FrED: laptop disconnected")
                self._fred_laptop = laptop_now

    def reset_stats(self) -> None:
        self.pinger.reset()
        self.fred.rtt.reset()
        session = self.relay.session if self.relay else None
        if session is not None:
            session.sync_rtt.reset()
        self.log.add("statistics reset")

    # ---- screen ------------------------------------------------------- #
    def screen_lines(self, log_lines: int = 8) -> List[Tuple[str, str]]:
        """Every dashboard line as (text, style); style: title/ok/warn/bad/dim
        or "" (normal)."""
        c = self.conf
        out: List[Tuple[str, str]] = []
        add = out.append
        add((f"FrED HUB (Pi 5)  {time.strftime('%H:%M:%S')}  "
             f"up {fmt_duration(time.time() - self.started)}"
             + ("  [SIM MODE]" if self.sim else "")
             + "    q quit, r reset stats", "title"))

        hs = self.net.hotspot_up
        state = ("UP" if hs else "waiting..." if hs is None else "DOWN")
        chan = f", channel {self.net.iface_info}" if self.net.iface_info else ""
        add((f"WiFi  {c['HUB_SSID']} / {c['HUB_PASSWORD']}  "
             f"hotspot {state}{chan}  hub {c['HUB_IP']}",
             "ok" if hs else "bad" if hs is False else "warn"))
        if self.net.hub_health:
            add((f"Hub   {self.net.hub_health}",
                 "bad" if self.net.hub_health_bad else ""))
        add(("", ""))

        # ---- FrED -------------------------------------------------------- #
        add(("FrED (Pi 4)", "title"))
        st_fred = self._station(self.fred_ip)
        _, fred_ping = self.pinger.stats("fred")
        fs = fred_ping.summary() if fred_ping else None
        add((f"  address {self.fred_ip}   {self._radio_text(st_fred)}",
             "" if st_fred else "warn"))
        ping_style = ("" if not fs or not fs["count"] else
                      "bad" if fred_ping.lost_in_a_row >= FRED_LOST_PINGS else
                      "warn" if (fs["loss"] or 0) > 10 else "ok")
        add((f"  ping    {RttStats.text(fs) if fs else 'waiting...'}",
             ping_style))
        reply = self.fred.reply if self.fred.answering else None
        if reply is None:
            add(("  program: no answer on the status port - is main.py "
                 "running (bash start_fred.sh)?", "warn"))
        else:
            up = reply.get("uptime_s")
            add((f"  program: {reply.get('program', 'main.py')} "
                 f"{reply.get('version', '')} answering "
                 f"({fmt_ms(self.fred.rtt.summary()['last'])} ms)"
                 + (f", running {fmt_duration(up)}" if up else ""), "ok"))
            laptop = reply.get("laptop")
            if laptop:
                how = ("through the HUB RELAY" if reply.get("laptop_via_hub")
                       else f"DIRECTLY from {laptop}")
                add((f"  laptop link: connected {how}", "ok"))
            else:
                add(("  laptop link: waiting for the laptop", "dim"))
            if reply.get("experiment"):
                add((f"  {reply['experiment']}", ""))
            load = reply.get("load")
            if load:
                add((f"  FrED load (10 s avg): {load.get('summary', '')}",
                     "bad" if load.get("bad") else ""))
                if load.get("threads"):
                    add((f"     main.py threads: {load['threads']}", "dim"))
        add(("", ""))

        # ---- Laptop and other devices ---------------------------------- #
        add(("LAPTOP", "title"))
        laptop_ip, laptop_stats = self.pinger.stats("laptop")
        if not laptop_ip:
            add(("  not identified yet - join the laptop to the "
                 f"'{c['HUB_SSID']}' WiFi and connect the app", "dim"))
        else:
            st = self._station(laptop_ip)
            name = f" ({st['name']})" if st and st.get("name") else ""
            add((f"  address {laptop_ip}{name}   {self._radio_text(st)}", ""))
            ls = laptop_stats.summary()
            all_lost = ls["count"] >= 3 and ls["loss"] == 100
            add((f"  ping    {RttStats.text(ls)}", "warn" if all_lost else
                 "ok" if ls["count"] else ""))
            if all_lost:
                add(("  (Windows blocks ping by default - see the hub README "
                     "to allow it; the link itself is not affected)", "dim"))
        others = [s for s in self.net.stations
                  if s.get("ip") not in (self.fred_ip, laptop_ip)]
        for st in others:
            name = f" {st['name']}" if st.get("name") else ""
            add((f"  other device: {st.get('ip') or st['mac']}{name}   "
                 f"{self._radio_text(st)}", "dim"))
        if self.net.iw_error:
            add((f"  ({self.net.iw_error})", "dim"))
        add(("", ""))

        # ---- Relay ---------------------------------------------------------- #
        if self.relay is None:
            add(("RELAY  off (--no-relay): use 'Hub - direct' on the laptop",
                 "title"))
        else:
            add((f"RELAY  laptop -> {c['HUB_IP']}:{self.relay_port} -> FrED "
                 f"{self.fred_ip}:{self.fred_port}", "title"))
            session = self.relay.session
            if not self.relay.listening:
                add((f"  not listening: {self.relay.error or 'starting...'}",
                     "bad" if self.relay.error else "warn"))
            elif session is None or not session.alive:
                add(("  idle - waiting for a laptop in 'Hub - relay' mode",
                     "dim"))
            else:
                snap = session.snapshot()
                since = time.strftime("%H:%M:%S",
                                      time.localtime(session.started))
                add((f"  ACTIVE since {since} with laptop {session.laptop_ip}"
                     + (f"   FrED phase: {session.fred_phase}"
                        if session.fred_phase else ""), "ok"))
                add((f"  laptop -> FrED: {self._counts_text(snap['up'])}, "
                     f"{fmt_bytes(snap['bytes']['up'])}", ""))
                add((f"  FrED -> laptop: {self._counts_text(snap['down'])}, "
                     f"{fmt_bytes(snap['bytes']['down'])}", ""))
                add((f"  sync pings, hub <-> FrED leg: "
                     f"{RttStats.text(session.sync_rtt.summary())}", ""))
            last = self.relay.last_session
            if last is not None and not last.alive:
                add((f"  last session ({last.laptop_ip}) ended: "
                     f"{last.close_reason}", "dim"))
        add(("", ""))

        add(("LOG", "title"))
        for line in self.log.recent(log_lines):
            add(("  " + line, ""))
        return out

    def _station(self, ip: Optional[str]) -> Optional[dict]:
        for st in self.net.stations:
            if ip and st.get("ip") == ip:
                return st
        return None

    @staticmethod
    def _radio_text(st: Optional[dict]) -> str:
        if st is None:
            return "not on the hub WiFi (yet)"
        parts = []
        if "signal" in st:
            sig = st["signal"]
            quality = ("excellent" if sig >= -50 else "good" if sig >= -60
                       else "fair" if sig >= -70 else "WEAK")
            parts.append(f"{sig} dBm ({quality})")
        if "tx_mbit" in st:
            parts.append(f"{st['tx_mbit']:.0f}/{st.get('rx_mbit', 0):.0f}"
                         " Mbit/s")
        if "connected_s" in st:
            parts.append(f"joined {fmt_duration(st['connected_s'])} ago")
        return ", ".join(parts) if parts else "on the WiFi"

    @staticmethod
    def _counts_text(counts: dict) -> str:
        if not counts:
            return "nothing yet"
        total = sum(counts.values())
        detail = ", ".join(f"{k} {v}" for k, v in
                           sorted(counts.items(), key=lambda kv: -kv[1]))
        return f"{total} msgs ({detail})"

    def plain_summary(self) -> str:
        """One line for --plain mode."""
        _, fred_ping = self.pinger.stats("fred")
        fs = fred_ping.summary() if fred_ping else {"count": 0}
        reply = self.fred.reply if self.fred.answering else None
        parts = [f"FrED ping {fmt_ms(fs.get('last'))} ms"
                 f" (median {fmt_ms(fs.get('median'))})" if fs["count"]
                 else "FrED ping waiting"]
        if reply is None:
            parts.append("FrED program: no answer")
        else:
            laptop = reply.get("laptop")
            parts.append("laptop " + (("via relay" if reply.get(
                "laptop_via_hub") else f"direct {laptop}") if laptop
                else "not connected"))
            load = reply.get("load")
            if load and load.get("summary"):
                parts.append(f"FrED {load['summary']}")
        session = self.relay.session if self.relay else None
        if session is not None and session.alive:
            s = session.sync_rtt.summary()
            parts.append(f"relay active, FrED leg {fmt_ms(s['median'])} ms")
        return f"{time.strftime('%H:%M:%S')}  " + "  |  ".join(parts)

    def close(self) -> None:
        if self.relay is not None:
            self.relay.close()
        self.fred.close()
        self.pinger.close()
        self.net.close()


# --------------------------------------------------------------------------- #
# Front ends: full-screen dashboard (curses) or plain log lines
# --------------------------------------------------------------------------- #
def wrap_lines(lines: List[Tuple[str, str]],
               width: int) -> List[Tuple[str, str]]:
    """Wrap long dashboard lines to the terminal width (continuations are
    indented under their line)."""
    out = []
    for text, style in lines:
        if len(text) <= width or width < 20:
            out.append((text, style))
            continue
        indent = " " * (len(text) - len(text.lstrip()) + 4)
        for part in textwrap.wrap(text, width, subsequent_indent=indent):
            out.append((part, style))
    return out


def run_dashboard(hub: Hub) -> None:
    import curses

    def main(stdscr) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        styles = {"": curses.A_NORMAL, "title": curses.A_BOLD,
                  "ok": curses.A_NORMAL, "warn": curses.A_BOLD,
                  "bad": curses.A_BOLD | curses.A_REVERSE, "dim": curses.A_DIM}
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                background = -1
            except curses.error:
                background = curses.COLOR_BLACK
            for n, color in ((1, curses.COLOR_CYAN), (2, curses.COLOR_GREEN),
                             (3, curses.COLOR_YELLOW), (4, curses.COLOR_RED)):
                curses.init_pair(n, color, background)
            styles.update({"title": curses.color_pair(1) | curses.A_BOLD,
                           "ok": curses.color_pair(2),
                           "warn": curses.color_pair(3) | curses.A_BOLD,
                           "bad": curses.color_pair(4) | curses.A_BOLD})
        stdscr.timeout(250)
        last = 0.0
        while True:
            key = stdscr.getch()
            if key in (ord("q"), ord("Q")):
                return
            if key in (ord("r"), ord("R")):
                hub.reset_stats()
            now = time.monotonic()
            if now - last < 1.0 and key not in (ord("r"), ord("R"),
                                                curses.KEY_RESIZE):
                continue
            last = now
            hub.tick()
            height, width = stdscr.getmaxyx()
            body = wrap_lines(hub.screen_lines(log_lines=0), width - 1)
            # The log (last section) gets whatever rows are left, newest
            # lines kept.
            room = height - 1 - len(body)
            log = wrap_lines([("  " + line, "") for line in
                              hub.log.recent(max(1, room))], width - 1)
            lines = body + (log[-room:] if room > 0 else [])
            stdscr.erase()
            for row, (text, style) in enumerate(lines[:max(0, height - 1)]):
                try:
                    stdscr.addnstr(row, 0, text, max(1, width - 1),
                                   styles.get(style, curses.A_NORMAL))
                except curses.error:
                    pass
            stdscr.refresh()

    curses.wrapper(main)


def run_plain(hub: Hub) -> None:
    print("FrED hub running (plain mode). Ctrl+C to stop.", flush=True)
    last = 0.0
    while True:
        hub.tick()
        now = time.monotonic()
        if now - last >= 10.0:
            last = now
            print(hub.plain_summary(), flush=True)
        time.sleep(1.0)


def check(conf: Dict[str, str], args) -> bool:
    """--check: is everything the hub needs available? (installer step)"""
    ok = True
    print(f"  conf  {CONF_FILE}: WiFi {conf['HUB_SSID']}, hub {conf['HUB_IP']}"
          f", FrED {conf['FRED_IP']}, port {conf['LINK_PORT']}")
    if not args.sim:
        for tool in ("nmcli", "iw", "ping", "ip"):
            found = shutil.which(tool) is not None
            print(f"  {'OK  ' if found else 'FAIL'}  {tool}")
            ok = ok and found
    try:
        import curses  # noqa: F401
        print("  OK    curses (dashboard)")
    except ImportError:
        print("  WARN  curses missing: use --plain")
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("0.0.0.0", int(conf["LINK_PORT"])))
        probe.close()
        print(f"  OK    relay port {conf['LINK_PORT']} is free")
    except OSError as exc:
        print(f"  WARN  relay port {conf['LINK_PORT']} busy ({exc}) - "
              "is hub.py already running?")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(
        description="FrED hub: WiFi dashboard + relay (Raspberry Pi 5).")
    parser.add_argument("--plain", action="store_true",
                        help="print log lines instead of the full-screen "
                             "dashboard")
    parser.add_argument("--no-relay", action="store_true",
                        help="monitor only; do not run the relay")
    parser.add_argument("--sim", action="store_true",
                        help="fake WiFi devices and pings (test on any PC); "
                             "FrED defaults to 127.0.0.1, relay to port "
                             "LINK_PORT+10")
    parser.add_argument("--check", action="store_true",
                        help="check the tools / settings and exit")
    parser.add_argument("--conf", default=CONF_FILE, help="settings file")
    parser.add_argument("--fred-ip", help="override FRED_IP")
    parser.add_argument("--fred-port", type=int, help="override LINK_PORT "
                        "(FrED side)")
    parser.add_argument("--relay-port", type=int,
                        help="port the relay listens on")
    parser.add_argument("--status-port", type=int, help="override STATUS_PORT")
    args = parser.parse_args()

    conf = load_conf(args.conf)
    if args.check:
        return 0 if check(conf, args) else 1

    hub = Hub(conf, args)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        if args.plain:
            run_plain(hub)
        else:
            try:
                run_dashboard(hub)
            except ImportError:
                print("curses is not available - falling back to --plain.")
                run_plain(hub)
    except KeyboardInterrupt:
        pass
    finally:
        hub.close()
        print("FrED hub stopped. (The hotspot keeps running; stop it with: "
              "bash hub_hotspot.sh down)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
