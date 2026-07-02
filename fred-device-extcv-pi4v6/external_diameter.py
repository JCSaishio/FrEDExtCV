"""Receive fiber diameter measurements streamed over WiFi from an external PC.

This is the **WiFi (TCP) variant** of the diameter link. The fiber diameter is
measured on a separate computer (see the *FrED Fiber Measure with Streaming v3*
program) and streamed to the Raspberry Pi over a wireless TCP socket instead of
a USB cable.

The Pi is the **server**: it runs as a WiFi hotspot (see ``setup_hotspot.sh``)
and listens on a TCP port. The laptop joins the hotspot and connects to the Pi
as a client, then streams measurements. A background thread accepts the client
and reads the incoming messages, exposing the most recent diameter.

:meth:`ExternalDiameter.update` is a drop-in replacement for the old
``FiberCamera.camera_feedback`` call: it pushes the latest reading into the same
:class:`~database.Database` buffers and the diameter plot, so the rest of the
device code does not need to know where the number came from.

Wire protocol (newline-delimited JSON, UTF-8 — identical to the USB version)::

    {"v": 1, "d": 0.352, "u": "mm", "t": 12.345, "found": true}

    v      protocol version (int)
    d      diameter value, in the units given by ``u``
    u      unit string ("mm" or "px")
    t      sender elapsed seconds (informational)
    found  whether a fiber was detected in that frame

A keepalive line ``{"v": 1, "hb": true}`` may also be sent. Malformed lines are
ignored. The link is optional: if no laptop connects, the interface still runs,
simply reporting zero diameter until a stream arrives.
"""
import json
import socket
import subprocess
import threading
import time
from typing import TYPE_CHECKING, List, Tuple

from database import Database

if TYPE_CHECKING:
    from PyQt5.QtWidgets import QDoubleSpinBox
    from user_interface import UserInterface

# --------------------------------------------------------------------------- #
# Hotspot / network defaults. These MUST match what setup_hotspot.sh configures
# and what the laptop app (FrED Fiber Measure with Streaming v3) is pre-filled
# with, so the connection details shown on screen are correct.
# --------------------------------------------------------------------------- #
HOTSPOT_SSID = "FrED_Pi"
HOTSPOT_PASSWORD = "fredfiber123"
HOTSPOT_IP = "192.168.4.1"       # the Pi's address while acting as the hotspot
STREAM_PORT = 5005               # TCP port the laptop streams to


class ExternalDiameter:
    """Receive streamed fiber diameter over a WiFi TCP socket (Pi = server)."""

    PROTOCOL_VERSION = 1
    STREAM_TIMEOUT = 2.0       # seconds without data before "no signal"

    def __init__(self, target_diameter: "QDoubleSpinBox", gui: "UserInterface",
                 host: str = "0.0.0.0", port: int = STREAM_PORT) -> None:
        self.target_diameter = target_diameter
        self.gui = gui
        self.host = host               # 0.0.0.0 -> listen on every interface
        self.port = port

        self.listening = False
        self.connected = False         # True while a laptop client is connected
        self.client_address = None     # (ip, port) of the connected laptop
        self.latest_diameter = 0.0
        self.latest_units = "mm"
        self.latest_found = False
        self.last_message_time = 0.0
        self.previous_time = 0.0

        self._server_sock = None
        self._client_sock = None
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()   # serialise writes back to laptop
        self._stop = threading.Event()
        self._buffer = b""

        self._thread = threading.Thread(target=self._serve_loop, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ #
    # Sending messages back to the laptop (status, recorded data)
    # ------------------------------------------------------------------ #
    def send_message(self, obj: dict) -> bool:
        """Send one newline-delimited JSON object to the connected laptop."""
        with self._send_lock:
            sock = self._client_sock
            if sock is None:
                return False
            try:
                sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))
                return True
            except Exception:
                return False

    def _send_recorded_data(self) -> None:
        """Respond to a laptop 'get_data' request with the experiment CSV."""
        experiment = getattr(self.gui, "experiment", None)
        payload = experiment.data_payload() if experiment else None
        if payload is None:
            self.send_message({"type": "event", "event": "no_data",
                               "message": "No experiment data available yet."})
        else:
            self.send_message(payload)

    # ------------------------------------------------------------------ #
    # Network helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def local_ip_addresses() -> List[str]:
        """Return this Pi's non-loopback IPv4 addresses (hotspot IP first)."""
        ips: List[str] = []
        try:
            out = subprocess.check_output(["hostname", "-I"], text=True)
            ips = [tok for tok in out.split() if "." in tok]
        except Exception:
            pass
        if not ips:
            # Fallback: ask the OS which address it would use to reach the net.
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                    probe.connect(("8.8.8.8", 80))
                    ips = [probe.getsockname()[0]]
            except Exception:
                ips = []
        # Show the hotspot address first if the Pi is running as the AP.
        ips.sort(key=lambda ip: (not ip.startswith("192.168.4."), ip))
        return ips

    def primary_ip(self) -> str:
        """Best guess at the address the laptop should connect to."""
        ips = self.local_ip_addresses()
        for ip in ips:
            if ip.startswith("192.168.4."):
                return ip
        return ips[0] if ips else HOTSPOT_IP

    # ------------------------------------------------------------------ #
    # Server / accept loop
    # ------------------------------------------------------------------ #
    def _open_server(self) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(1.0)
            sock.bind((self.host, self.port))
            sock.listen(1)
            self._server_sock = sock
            self.listening = True
            print(f"[ExternalDiameter] Listening on {self.host}:{self.port}")
            return True
        except Exception as exc:
            print(f"[ExternalDiameter] Could not open server socket: {exc}")
            self.listening = False
            return False

    def _serve_loop(self) -> None:
        # Keep trying to (re)open the listening socket until we succeed or stop.
        while not self._stop.is_set() and not self.listening:
            if not self._open_server():
                time.sleep(2.0)

        while not self._stop.is_set():
            try:
                client, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except Exception as exc:
                print(f"[ExternalDiameter] Accept error: {exc}")
                time.sleep(1.0)
                continue

            print(f"[ExternalDiameter] Laptop connected from {addr[0]}:{addr[1]}")
            client.settimeout(0.5)
            with self._lock:
                self._client_sock = client
                self.connected = True
                self.client_address = addr
                self._buffer = b""
            self._read_client(client)
            # Client disconnected -> back to waiting for a new one.
            with self._lock:
                self.connected = False
                self.client_address = None
                self._client_sock = None
            try:
                client.close()
            except Exception:
                pass
            print("[ExternalDiameter] Laptop disconnected; waiting for a new "
                  "connection...")

    def _read_client(self, client: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                data = client.recv(512)
            except socket.timeout:
                continue
            except Exception as exc:
                print(f"[ExternalDiameter] Read error: {exc}")
                return
            if not data:        # peer closed the connection
                return
            self._buffer += data
            while b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                self._handle_line(line)

    def _handle_line(self, raw: bytes) -> None:
        try:
            text = raw.decode("utf-8", errors="ignore").strip()
            if not text:
                return
            message = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            return  # ignore malformed lines / partial frames

        # Experiment commands from the laptop (v6).
        mtype = message.get("type")
        if mtype:
            self._handle_command(mtype, message)
            return

        if message.get("hb"):  # heartbeat / keepalive
            with self._lock:
                self.last_message_time = time.time()
            return
        if "d" not in message:
            return
        try:
            diameter = float(message.get("d", 0.0))
        except (TypeError, ValueError):
            return
        with self._lock:
            self.latest_diameter = diameter
            self.latest_units = message.get("u", "mm")
            self.latest_found = bool(message.get("found", True))
            self.last_message_time = time.time()

    def _handle_command(self, mtype: str, message: dict) -> None:
        """Dispatch an experiment command received from the laptop."""
        experiment = getattr(self.gui, "experiment", None)
        if mtype == "experiment":
            if experiment is not None:
                experiment.start(message.get("params", {}))
        elif mtype == "abort":
            if experiment is not None:
                experiment.abort()
        elif mtype == "get_data":
            self._send_recorded_data()
        # unknown types are ignored

    # ------------------------------------------------------------------ #
    # Public access
    # ------------------------------------------------------------------ #
    def get_latest(self) -> Tuple[float, bool, float]:
        """Return (diameter, found, seconds_since_last_message)."""
        with self._lock:
            age = time.time() - self.last_message_time if self.last_message_time else float("inf")
            return self.latest_diameter, self.latest_found, age

    def is_streaming(self) -> bool:
        """True if a laptop is connected and a message arrived recently."""
        with self._lock:
            if not self.connected or not self.last_message_time:
                return False
            return (time.time() - self.last_message_time) < self.STREAM_TIMEOUT

    def connection_info(self) -> dict:
        """Everything the GUI needs to show the user how to connect."""
        with self._lock:
            client = (f"{self.client_address[0]}:{self.client_address[1]}"
                      if self.client_address else None)
        return {
            "ssid": HOTSPOT_SSID,
            "password": HOTSPOT_PASSWORD,
            "ip": self.primary_ip(),
            "all_ips": self.local_ip_addresses(),
            "port": self.port,
            "listening": self.listening,
            "client": client,
        }

    def status_text(self) -> str:
        """Human-readable status string for the GUI."""
        if not self.listening:
            return f"Diameter link: starting WiFi server on port {self.port}..."
        if not self.connected:
            return (f"Diameter link: waiting for laptop on "
                    f"{self.primary_ip()}:{self.port}")
        if self.is_streaming():
            diameter, found, _ = self.get_latest()
            if found:
                return f"Diameter link: streaming - {diameter:.4f} mm"
            return "Diameter link: streaming - no fiber detected"
        return "Diameter link: laptop connected, waiting for data..."

    # ------------------------------------------------------------------ #
    # Hardware-loop hook (replaces FiberCamera.camera_feedback)
    # ------------------------------------------------------------------ #
    def update(self, current_time: float) -> None:
        """Feed the latest streamed diameter into the plot and the database."""
        try:
            diameter, _found, _age = self.get_latest()
            target = self.gui.get_target_diameter()

            self.gui.diameter_plot.update_plot(current_time, diameter, target)

            Database.camera_timestamps.append(current_time)
            Database.diameter_readings.append(diameter)
            Database.diameter_setpoint.append(target)
            Database.diameter_delta_time.append(current_time - self.previous_time)
            self.previous_time = current_time
        except Exception as exc:
            print(f"Error in external diameter update: {exc}")

    def close(self) -> None:
        """Stop the server thread and release the sockets."""
        self._stop.set()
        for sock in (self._client_sock, self._server_sock):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        self._client_sock = None
        self._server_sock = None
        self.listening = False
        self.connected = False
