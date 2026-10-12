"""WiFi command link between the FrED Pi and the laptop (v7).

Since v7 the fiber diameter is measured, graphed and recorded entirely on the
laptop - it is NOT streamed to the Pi any more, so the Pi's CPU goes to the
temperature / spooler control loops and their two graphs. Once an experiment
has been sent, this link is practically dormant: it only carries a few short
command messages and a clock-sync ping every few seconds.

The Pi is the **server**: it listens on a TCP port and the laptop connects as
a client. The WiFi network comes from one of two places (``setup_wifi.sh``):

* **the hub** (normal): a separate Raspberry Pi 5 runs the ``FrED_Hub``
  hotspot (``fred-hub-pi5/``); this Pi joins it at the fixed address
  ``FRED_HUB_IP``. The laptop connects either straight to that address
  ("Hub - direct") or to the hub, whose relay forwards every byte to this
  server ("Hub - relay"; the connection then comes from ``HUB_IP``).
* **its own hotspot** (fallback, the old way): this Pi is the ``FrED_Pi``
  hotspot at ``HOTSPOT_IP`` (``setup_hotspot.sh``).

This program is the same either way. Messages are newline-delimited JSON
objects with a ``type`` field.

Laptop -> Pi::

    {"type": "experiment", "params": {...}}   start an automated run
    {"type": "start_now", "params": {...}}    "Start recording now": jump
                                              straight to RECORDING (or, with
                                              no run active, start this run
                                              directly in RECORDING)
    {"type": "abort"}                         stop every system
    {"type": "get_data"}                      send the recorded table back
    {"type": "sync", "id": n, "t1": t}        clock-sync ping

Pi -> laptop::

    {"type": "status", "phase", "remaining", "message", "data_ready", ["t0"]}
    {"type": "data", "name", "b64", "meta"}   recorded table (CSV, base64)
    {"type": "event", "event": "no_data", "message"}
    {"type": "sync_reply", "id", "t1", "t2", "t3", "boot"}

Clock sync
----------
Each machine timestamps its own samples on its own monotonic clock. The laptop
sends a ping stamped t1 (laptop clock); the Pi stamps when the ping arrived
(t2) and when the reply leaves (t3) on its experiment clock (``gui.now()``, the
same clock as every recorded row); the laptop stamps the reply's arrival (t4).
NTP maths then gives the Pi-minus-laptop offset ((t2 - t1) + (t3 - t4)) / 2
with an error bound of half the network round trip. That lets the laptop place
the Pi's recording start (``t0``, reported in Pi time) on its own clock, so the
camera data and FrED's data share the same t = 0 regardless of WiFi latency.
``boot`` identifies this run of the Pi program (i.e. its clock origin) so sync
samples from an earlier Pi session are never mixed in.

Diameter lines sent by an older laptop app (``{"v": 1, "d": ...}``) are
ignored.

Hub status ping (UDP ``STATUS_PORT``)
-------------------------------------
The hub's dashboard sends ``{"type": "hub_ping", "id": n}`` every 2 s on a
separate UDP port, so it never touches the laptop's single TCP connection.
This program answers ``{"type": "hub_pong", "id": n, ...}`` with whether a
laptop is connected (and whether through the relay), the experiment status
and the Pi's CPU load (``pi_load.py``).
"""
import json
import socket
import subprocess
import threading
import time
import uuid
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from user_interface import UserInterface

# --------------------------------------------------------------------------- #
# Network defaults. These MUST match setup_hotspot.sh / setup_wifi.sh, the
# hub's hub.conf (fred-hub-pi5/) and the laptop app's presets, so the
# connection details shown on screen are correct.
# --------------------------------------------------------------------------- #
# FrED's own hotspot (fallback: bash setup_wifi.sh hotspot)
HOTSPOT_SSID = "FrED_Pi"
HOTSPOT_PASSWORD = "fredfiber123"
HOTSPOT_IP = "192.168.4.1"       # the Pi's address while acting as the hotspot
# The hub Pi 5's network (normal: bash setup_wifi.sh hub)
HUB_SSID = "FrED_Hub"
HUB_PASSWORD = "fredfiber123"
HUB_IP = "192.168.50.1"          # the hub; relayed connections come from here
FRED_HUB_IP = "192.168.50.2"     # this Pi's fixed address on the hub
LINK_PORT = 5005                 # TCP port the laptop connects to
STATUS_PORT = 5006               # UDP port answering the hub's status pings


class LaptopLink:
    """Serve the laptop's command connection over WiFi (Pi = server)."""

    READ_TIMEOUT = 0.5         # recv poll period on the client socket
    SEND_TIMEOUT = 30.0        # deadline for one outgoing message (the
                               # recorded CSV is >1 MB in a single line and
                               # can NOT finish within READ_TIMEOUT on WiFi)
    IP_CACHE_S = 10.0          # re-query the Pi's IP addresses at most this
                               # often (it spawns a process, and the status
                               # label refreshes twice a second)

    def __init__(self, gui: "UserInterface", host: str = "0.0.0.0",
                 port: int = LINK_PORT) -> None:
        self.gui = gui
        self.host = host               # 0.0.0.0 -> listen on every interface
        self.port = port
        self.boot_id = uuid.uuid4().hex[:12]   # this Pi session's clock origin

        self.listening = False
        self.connected = False         # True while a laptop client is connected
        self.client_address = None     # (ip, port) of the connected laptop
        self.last_message_time = 0.0   # time.monotonic() of the last message

        self._ips: List[str] = []
        self._ips_time = float("-inf")

        self._server_sock = None
        self._client_sock = None
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()   # serialise writes back to laptop
        self._stop = threading.Event()
        self._buffer = b""

        self._thread = threading.Thread(target=self._serve_loop,
                                        name="laptop-link", daemon=True)
        self._thread.start()
        self._status_sock = None
        self._status_thread = threading.Thread(target=self._status_loop,
                                               name="hub-status", daemon=True)
        self._status_thread.start()

    # ------------------------------------------------------------------ #
    # Sending messages back to the laptop (status, recorded data, sync)
    # ------------------------------------------------------------------ #
    def send_message(self, obj: dict) -> bool:
        """Send one newline-delimited JSON object to the connected laptop."""
        with self._send_lock:
            sock = self._client_sock
            if sock is None:
                return False
            try:
                payload = (json.dumps(obj) + "\n").encode("utf-8")
                # The socket normally carries the short READ_TIMEOUT (recv
                # polling). sendall() of a large payload (the recorded CSV)
                # cannot finish within it over WiFi, and a timed-out sendall
                # leaves a TRUNCATED line on the wire that corrupts every
                # message after it. Give sends their own generous deadline.
                sock.settimeout(self.SEND_TIMEOUT)
                try:
                    sock.sendall(payload)
                finally:
                    sock.settimeout(self.READ_TIMEOUT)
                return True
            except Exception as exc:
                # An unknown prefix of the message may already be on the wire,
                # so the stream is unrecoverable - close the connection so the
                # laptop sees a clean disconnect and can reconnect fresh,
                # instead of silently receiving garbage forever.
                print(f"[LaptopLink] Send failed ({exc}); closing the client "
                      "connection so the laptop can reconnect.")
                try:
                    sock.close()
                except Exception:
                    pass
                return False

    def _send_recorded_data(self) -> None:
        """Respond to a laptop 'get_data' request with the experiment table."""
        experiment = getattr(self.gui, "experiment", None)
        payload = experiment.data_payload() if experiment else None
        if payload is None:
            self.send_message({"type": "event", "event": "no_data",
                               "message": "No experiment data available yet."})
        else:
            self.send_message(payload)

    def _reply_sync(self, message: dict, t_arrival: float) -> None:
        """Answer a clock-sync ping straight away (see the module doc)."""
        self.send_message({
            "type": "sync_reply",
            "id": message.get("id"),
            "t1": message.get("t1"),
            "t2": t_arrival,             # ping arrived (Pi experiment clock)
            "t3": self.gui.now(),        # reply leaves (Pi experiment clock)
            "boot": self.boot_id,
        })

    # ------------------------------------------------------------------ #
    # Network helpers
    # ------------------------------------------------------------------ #
    def local_ip_addresses(self) -> List[str]:
        """Return this Pi's non-loopback IPv4 addresses (hub / hotspot first).

        Cached for IP_CACHE_S seconds: ``hostname -I`` spawns a process, and
        the status label is refreshed twice a second.
        """
        now = time.monotonic()
        if now - self._ips_time < self.IP_CACHE_S:
            return list(self._ips)
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
        # The hub / hotspot address first: that is the one the laptop uses.
        ips.sort(key=lambda ip: (ip not in (FRED_HUB_IP, HOTSPOT_IP), ip))
        self._ips, self._ips_time = ips, now
        return list(ips)

    def wifi_mode(self) -> str:
        """"hub" (joined the hub Pi 5), "hotspot" (this Pi is FrED_Pi) or
        "other" (neither: see bash setup_wifi.sh)."""
        ips = self.local_ip_addresses()
        if FRED_HUB_IP in ips:
            return "hub"
        if HOTSPOT_IP in ips:
            return "hotspot"
        return "other"

    def primary_ip(self) -> str:
        """Best guess at the address the laptop should connect to."""
        ips = self.local_ip_addresses()
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
            print(f"[LaptopLink] Listening on {self.host}:{self.port}")
            return True
        except Exception as exc:
            print(f"[LaptopLink] Could not open server socket: {exc}")
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
                print(f"[LaptopLink] Accept error: {exc}")
                time.sleep(1.0)
                continue

            print(f"[LaptopLink] Laptop connected from {addr[0]}:{addr[1]}")
            client.settimeout(self.READ_TIMEOUT)
            # Small messages (sync replies) must leave immediately.
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                self._client_sock = client
                self.connected = True
                self.client_address = addr
                self._buffer = b""
            experiment = getattr(self.gui, "experiment", None)
            if experiment is not None:
                experiment.announce()    # laptop learns the current phase
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
            print("[LaptopLink] Laptop disconnected; waiting for a new "
                  "connection...")

    def _read_client(self, client: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                data = client.recv(4096)
            except socket.timeout:
                continue
            except Exception as exc:
                print(f"[LaptopLink] Read error: {exc}")
                return
            if not data:        # peer closed the connection
                return
            t_arrival = self.gui.now()    # stamped for clock-sync pings
            self._buffer += data
            while b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                self._handle_line(line, t_arrival)

    def _handle_line(self, raw: bytes, t_arrival: float) -> None:
        try:
            text = raw.decode("utf-8", errors="ignore").strip()
            if not text:
                return
            message = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            return  # ignore malformed lines / partial frames
        if not isinstance(message, dict):
            return
        with self._lock:
            self.last_message_time = time.monotonic()
        mtype = message.get("type")
        if mtype:
            self._handle_command(mtype, message, t_arrival)
        # Anything without a type (e.g. diameter lines from an older laptop
        # app) is ignored: the diameter now stays on the laptop.

    def _handle_command(self, mtype: str, message: dict,
                        t_arrival: float) -> None:
        """Dispatch a command received from the laptop."""
        if mtype == "sync":
            self._reply_sync(message, t_arrival)
            return
        experiment = getattr(self.gui, "experiment", None)
        if experiment is None:
            return
        if mtype == "experiment":
            experiment.start(message.get("params", {}))
        elif mtype == "start_now":
            experiment.start_now(message.get("params", {}))
        elif mtype == "abort":
            experiment.abort()
        elif mtype == "get_data":
            self._send_recorded_data()
        # unknown types are ignored

    # ------------------------------------------------------------------ #
    # Public access
    # ------------------------------------------------------------------ #
    def via_hub(self) -> bool:
        """True while the laptop is connected through the hub's relay."""
        with self._lock:
            return bool(self.client_address) and \
                self.client_address[0] == HUB_IP

    def link_description(self) -> str:
        """How the laptop reaches FrED right now, for the run's metadata.
        Uses the cached addresses only (never spawns a process), so the
        hardware thread may call it."""
        if self.via_hub():
            return "hub relay"
        ips = list(self._ips)
        if FRED_HUB_IP in ips:
            return "hub direct" if self.connected else "hub"
        if HOTSPOT_IP in ips:
            return "FrED hotspot"
        return "other network"

    def connection_info(self) -> dict:
        """Everything the GUI needs to show the user how to connect."""
        with self._lock:
            client = (f"{self.client_address[0]}:{self.client_address[1]}"
                      if self.client_address else None)
        mode = self.wifi_mode()
        hub = mode == "hub"
        return {
            "mode": mode,
            "ssid": HUB_SSID if hub else HOTSPOT_SSID,
            "password": HUB_PASSWORD if hub else HOTSPOT_PASSWORD,
            "hub_ip": HUB_IP,
            "ip": self.primary_ip(),
            "all_ips": self.local_ip_addresses(),
            "port": self.port,
            "listening": self.listening,
            "client": client,
            "via_hub": self.via_hub(),
        }

    def status_text(self) -> str:
        """Human-readable status string for the GUI."""
        if not self.listening:
            return f"Laptop link: starting WiFi server on port {self.port}..."
        if not self.connected:
            return (f"Laptop link: waiting for laptop on "
                    f"{self.primary_ip()}:{self.port}")
        how = "through the hub relay" if self.via_hub() else "directly"
        return (f"Laptop link: connected {how} (commands only - the diameter "
                "is measured and recorded on the laptop)")

    # ------------------------------------------------------------------ #
    # Hub status ping (UDP) - see the module doc
    # ------------------------------------------------------------------ #
    def _hub_pong(self, ping_id) -> dict:
        with self._lock:
            client = (f"{self.client_address[0]}:{self.client_address[1]}"
                      if self.client_address else None)
        experiment = getattr(self.gui, "experiment", None)
        monitor = getattr(self.gui, "load_monitor", None)
        return {
            "type": "hub_pong",
            "id": ping_id,
            "program": "main.py",
            "version": "v7",
            "boot": self.boot_id,
            "uptime_s": round(self.gui.now(), 1),
            "laptop": client,
            "laptop_via_hub": self.via_hub(),
            "phase": experiment.phase if experiment else None,
            "experiment": experiment.status_line() if experiment else None,
            "load": monitor.hub_report() if monitor else None,
        }

    def _status_loop(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, STATUS_PORT))
            sock.settimeout(1.0)
        except OSError as exc:
            print(f"[LaptopLink] Hub status port {STATUS_PORT} unavailable "
                  f"({exc}); the hub will show 'no answer'.")
            return
        self._status_sock = sock
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return              # socket closed by close()
            try:
                message = json.loads(data.decode("utf-8", errors="ignore"))
                if not isinstance(message, dict) or \
                        message.get("type") != "hub_ping":
                    continue
                reply = self._hub_pong(message.get("id"))
                sock.sendto(json.dumps(reply).encode("utf-8"), addr)
            except Exception as exc:     # never let the hub break the link
                print(f"[LaptopLink] Hub status reply failed: {exc}")

    def close(self) -> None:
        """Stop the server thread and release the sockets."""
        self._stop.set()
        for sock in (self._client_sock, self._server_sock, self._status_sock):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        self._client_sock = None
        self._server_sock = None
        self._status_sock = None
        self.listening = False
        self.connected = False
