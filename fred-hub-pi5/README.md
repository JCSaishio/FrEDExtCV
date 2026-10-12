# $${\color{red}MIT}$$ $${\color{red}FrED}$$ — WiFi hub (Raspberry Pi 5)

A separate **Raspberry Pi 5** that runs the WiFi network **`FrED_Hub`**. FrED's
Raspberry Pi 4 and the laptop both join it, so FrED's Pi 4 no longer has to be
the hotspot itself. The hub also runs a terminal **dashboard** that pings both
devices and shows their WiFi signal, FrED's program state and CPU load, and an
optional **relay** that carries the laptop ↔ FrED messages through the hub.

It works like the other two folders: clone the repository, run the
installer once, then start it with the app runner:

```bash
cd fred-hub-pi5
bash setup_install.sh     # once (with internet): tools + the hotspot (starts at every boot)
bash start_hub.sh         # dashboard + relay
```

Partners: the FrED program in
[`../fred-device-extcv-pi4v7/`](../fred-device-extcv-pi4v7/) (Pi 4) and the
laptop app in
[`../FrEDFiberMeasurewithStreamingv7/`](../FrEDFiberMeasurewithStreamingv7/).

---

## Contents

1. [What the hub does, and why](#1-what-the-hub-does-and-why)
2. [What you need](#2-what-you-need)
3. [First-time setup](#3-first-time-setup)
4. [Every-day use](#4-every-day-use)
5. [The dashboard, line by line](#5-the-dashboard-line-by-line)
6. [Hub - direct vs. Hub - relay](#6-hub---direct-vs-hub---relay)
7. [Settings (`hub.conf`)](#7-settings-hubconf)
8. [Command reference](#8-command-reference)
9. [What changes on FrED and on the laptop](#9-what-changes-on-fred-and-on-the-laptop)
10. [Troubleshooting](#10-troubleshooting)
11. [Files](#11-files)
12. [Status](#12-status)

---

## 1. What the hub does, and why

Until now FrED's Pi 4 was the WiFi **hotspot**: besides the heater, stepper,
spooler, fan, graphs and experiments, it also ran the access point, DHCP and
DNS for the laptop. The hub moves that job to its own Pi 5:

```
 Hub - direct   LAPTOP ─WiFi─► HUB Pi 5 ─WiFi─► FrED 192.168.50.2:5005
 (default)                     (FrED_Hub, 192.168.50.1: forwards the packets;
                                the dashboard pings and watches both devices)

 Hub - relay    LAPTOP ─WiFi─► HUB 192.168.50.1:5005 ─► relay (hub.py) ─WiFi─► FrED 192.168.50.2:5005
                               (copies every message unchanged, counts and times them)

 FrED hotspot   LAPTOP ─WiFi FrED_Pi─► FrED 192.168.4.1:5005      (fallback, the old way, no hub)
```

| | FrED hotspot (old) | With the hub |
|---|---|---|
| Runs the WiFi access point, DHCP, DNS | FrED's Pi 4 | **hub Pi 5** |
| FrED's Pi 4 on the WiFi | access point | plain client (fixed `192.168.50.2`) |
| Laptop joins | `FrED_Pi` | **`FrED_Hub`** |
| Internet for FrED / laptop | none | yes, if the hub has an ethernet cable with internet |
| Network health visible | no | **dashboard**: signal, ping, FrED's program + CPU |

You can switch between all three paths at any time (FrED: `bash setup_wifi.sh
hub|hotspot`; laptop: the *Network* box) to find which works best. The
messages are exactly the same in all three.

**Honest expectation:** much of an access point's work is done by the WiFi
chip itself, so moving the hotspot may free less CPU than hoped. That is why
FrED now has a **Pi load (CPU)** box: measure first, then decide (FrED's
README, section 14, has the step-by-step comparison).

---

## 2. What you need

- A **Raspberry Pi 5** with **Raspberry Pi OS Bookworm or newer** (Desktop
  or Lite, 64-bit). Its built-in WiFi becomes the hotspot.
- Its power supply. The official 27 W (5.1 V / 5 A) is ideal; a good 5 V / 3 A
  supply also works for the hub, which draws little. The dashboard warns on
  under-voltage.
- For the **first** setup: internet (ethernet is easiest) and a screen +
  keyboard, or SSH.
- Optional: an **ethernet cable with internet** at all times. The hotspot then
  shares it, so FrED can `git pull` and the laptop keeps internet while on
  `FrED_Hub`.

Nothing is installed with pip: `hub.py` uses only Python's standard library.

---

## 3. First-time setup

**1. Get the code on the Pi 5** (same repository as FrED):

```bash
git clone https://github.com/JCSaishio/FrEDExtCV.git
cd FrEDExtCV/fred-hub-pi5
```

(Already cloned? `cd FrEDExtCV && git pull`.)

**2. Run the installer** (with internet):

```bash
bash setup_install.sh
```

It (1) synchronizes the clock (a wrong date breaks apt), (2) installs the few
system tools used (`iw`, `ping`, `rfkill`, NetworkManager; most are already
there), (3) **creates the `FrED_Hub` hotspot** and makes it start at every
boot, and (4) checks everything. Step 3 switches the Pi 5's WiFi into hotspot
mode, so it comes last. **If you are logged in over WiFi (SSH), that session
ends there** — the script warns you and waits 5 s. To install without
switching yet: `bash setup_install.sh --no-hotspot`, then later
`bash hub_hotspot.sh`.

**3. Put FrED on the hub** (on FrED's Pi 4, once):

```bash
cd FrEDExtCV && git pull
cd fred-device-extcv-pi4v7
bash setup_wifi.sh hub
```

**4. Laptop:** update the app (`git pull` / copy), join **`FrED_Hub`**
(password `fredfiber123`), tick *Connect automatically*.

---

## 4. Every-day use

1. **Switch on the hub.** The `FrED_Hub` hotspot starts by itself at boot.
2. **Start the dashboard** (and relay) on the hub:
   ```bash
   cd ~/FrEDExtCV/fred-hub-pi5
   bash start_hub.sh
   ```
   It checks that the hotspot is up (and starts it if not), then opens the
   dashboard. **q** quits (the hotspot keeps running), **r** resets the ping
   and relay statistics.
3. **Switch on FrED** and run `bash start_fred.sh` as usual. It joins the hub
   by itself (it keeps looking until the hub appears, so the order does not
   matter). It shows up on the dashboard as *FrED (Pi 4)*.
4. **Laptop:** join `FrED_Hub`, open the app, *Network* = **Hub - direct to
   FrED** (or **Hub - relay through the hub**), **Connect**.

The dashboard is optional for *Hub - direct* (the hotspot works without it),
but **required for *Hub - relay*** (the relay is part of `hub.py`).

---

## 5. The dashboard, line by line

```
FrED HUB (Pi 5)  14:03:44  up 0:12:31    q quit, r reset stats
WiFi  FrED_Hub / fredfiber123  hotspot UP, channel 6 (2437 MHz)  hub 192.168.50.1
Hub   CPU 3%  52 C  power OK

FrED (Pi 4)
  address 192.168.50.2   -41 dBm (excellent), 72/65 Mbit/s, joined 0:10:02 ago
  ping    2.1 ms now, median 3.0, max 9.8, lost 0%
  program: main.py v7 answering (2.4 ms), running 0:09:40
  laptop link: connected DIRECTLY from 192.168.50.23:51234
  Experiment: recording (120s left)
  FrED load (10 s avg): Pi CPU 38%  |  main.py 92% of one core  |  rest of system 15%  |  61 C, 1500 MHz, power OK
     main.py threads: GUI + graphs 61%, hardware loop 28%, network 1%, other 2%

LAPTOP
  address 192.168.50.23 (LAPTOP-ABC)   -55 dBm (excellent), 144/130 Mbit/s, joined 0:08:11 ago
  ping    3.0 ms now, median 4.1, max 12.0, lost 0%

RELAY  laptop -> 192.168.50.1:5005 -> FrED 192.168.50.2:5005
  idle - waiting for a laptop in 'Hub - relay' mode

LOG
  14:01:02  joined the WiFi: FrED (192.168.50.2)
  14:01:05  FrED program (main.py) is answering
  14:02:10  FrED: laptop connected DIRECTLY from 192.168.50.23:51234
```

| Line | What it tells you |
|---|---|
| **WiFi** | network name / password, whether the hotspot is **UP**, its channel (2.4 GHz = 1–13, 5 GHz = 36+) |
| **Hub** | the Pi 5's own CPU, temperature and power (`vcgencmd`). *UNDER-VOLTAGE* = weak supply |
| **FrED → address** | FrED's fixed address and its radio link: **signal** in dBm (−30 to −50 excellent, −50 to −60 good, −60 to −70 fair, below −70 **WEAK**: move the Pis closer / turn antennas), link speed (Mbit/s, hub→FrED / FrED→hub) and how long it has been on the WiFi. *not on the hub WiFi (yet)*: FrED has not joined |
| **FrED → ping** | ICMP ping every 2 s: latest, median and worst of the last 30, % lost. 3 lost in a row → logged as *FrED does NOT answer ping* |
| **FrED → program** | FrED's `main.py` answered the hub's **status ping** (UDP 5006) — so the program, not only the Pi, is running; and since when |
| **FrED → laptop link** | FrED's own view: *connected DIRECTLY from …* (Hub - direct or FrED hotspot), *connected through the HUB RELAY*, or *waiting for the laptop* |
| **FrED → Experiment** | the run's phase and time left |
| **FrED load** | FrED's **Pi load (CPU)** box, 10 s average (FrED README section 14 explains every number) |
| **LAPTOP** | the laptop's address, name (from DHCP), signal and ping. Windows blocks ping by default → *lost 100%*; see section 9 to allow it (the link is not affected either way) |
| **other device** | anything else on `FrED_Hub` (e.g. a phone) |
| **RELAY** | *idle* / *ACTIVE since … with laptop …* + FrED's phase; messages per type each way and bytes; **sync pings, hub ↔ FrED leg** = the part of the laptop's clock-sync round trip between the hub and FrED; *last session … ended: reason* |
| **LOG** | devices joining / leaving, FrED (un)reachable, program up/down, laptop connect/disconnect, relay sessions and refusals |

Over SSH on a narrow window, long lines wrap. For a log you can keep (or run
in the background) use `bash start_hub.sh --plain`: it prints the events as
they happen plus one summary line every 10 s.

---

## 6. Hub - direct vs. Hub - relay

**Hub - direct** (laptop → `192.168.50.2:5005`). The hub's WiFi chip and
Linux kernel forward the packets between the laptop and FrED; no program on
the hub touches them. The dashboard only watches.

**Hub - relay** (laptop → `192.168.50.1:5005`). `hub.py` accepts the laptop,
opens its **own** connection to FrED, and copies the bytes in both directions
unchanged (it only peeks at the start of each message to count it). So
experiments, START RECORDING NOW, abort, Retrieve Data and the clock sync work
exactly as in direct mode; FrED's screen says *connected through the hub
relay*. Rules:

- **One laptop at a time** (FrED serves one): a second laptop gets a *Hub
  relay* message and is disconnected; so does a laptop trying the relay while
  another laptop is connected to FrED directly (the hub asks FrED at that
  moment).
- If FrED cannot be reached, the laptop gets a *Hub relay* message saying so
  (is FrED on the hub WiFi, is `start_fred.sh` running?).
- If the laptop goes silent for **20 s** (it normally pings every 2 s — e.g.
  its WiFi dropped), the hub closes the session, which frees FrED for the
  next connection.
- When either side closes, the other side is closed too, so the laptop shows
  *Link lost* and can reconnect.

**Which is better?** By design, direct has one step less, and on this
development PC the relay added ~0.2–0.6 ms to a round trip — tiny next to
WiFi's own delays. Measure it on the real setup:

| Look at | Where | Better = |
|---|---|---|
| round trip, clock sync ± | laptop status line, each run's *Run info* | lower and steadier |
| achieved rate, FrED CPU | FrED's Sampling line and Pi load box, *Run info* | (FrED's load should not differ between the two) |
| lost pings, signal | this dashboard | 0 % lost, ≥ −60 dBm |

The full before/after procedure (FrED hotspot vs. Hub - direct vs. Hub -
relay) is in FrED's README, section 14.

---

## 7. Settings (`hub.conf`)

Every network setting of the hub is in **`hub.conf`** (read by the bash
scripts and `hub.py`):

| Key | Default | Meaning |
|---|---|---|
| `HUB_SSID` / `HUB_PASSWORD` | `FrED_Hub` / `fredfiber123` | the WiFi |
| `HUB_IP` | `192.168.50.1` | the hub (relay mode connects here) |
| `FRED_IP` | `192.168.50.2` | FrED's fixed address on the hub (direct mode connects here). The hub's DHCP hands out `.10`–`.254`, never `.2`–`.9` |
| `LINK_PORT` | `5005` | FrED's command port; the relay listens on the same number |
| `STATUS_PORT` | `5006` (UDP) | FrED answers the dashboard's status pings here |
| `WIFI_COUNTRY` | `MX` | the hotspot does not start without a country code |
| `BAND` / `CHANNEL` | `bg` / auto | `bg` = 2.4 GHz. For **5 GHz** set `BAND="a"` (channel 36 is used when `CHANNEL` is empty) — usually less crowded on campus; FrED's Pi 4 and most laptops support it |
| `IFACE` / `CON_NAME` | `wlan0` / `FrED_Hub_AP` | the WiFi interface / NetworkManager profile name |

After changing `hub.conf`, apply it with `bash hub_hotspot.sh` (and restart
`start_hub.sh`). **Changing the name, password or addresses?** Change them on
the other machines too — FrED: `setup_wifi.sh` and `laptop_link.py` (`HUB_*`,
`FRED_HUB_IP`), then `bash setup_wifi.sh hub` again; laptop:
`fiber_measure.py` (`HUB_*`, `FRED_HUB_IP`).

---

## 8. Command reference

| Command | Does |
|---|---|
| `bash setup_install.sh [--no-hotspot]` | first-time install (section 3) |
| `bash start_hub.sh` | hotspot check + dashboard + relay |
| `bash start_hub.sh --plain` | log lines instead of the full-screen dashboard |
| `bash start_hub.sh --no-relay` | dashboard only (no relay listening) |
| `bash start_hub.sh --sim` | dry run with fake WiFi devices (works on any PC with Python 3; the relay then listens on 5015 and expects FrED on 127.0.0.1) |
| `bash hub_hotspot.sh` | create / update + start the hotspot from `hub.conf` |
| `bash hub_hotspot.sh status` | is the hotspot up, its channel, the Pi's addresses |
| `bash hub_hotspot.sh down` | stop the hotspot (back to normal WiFi until the next boot) |
| `python3 hub.py --check` | check the tools / settings / relay port |
| `python3 hub.py --help` | every option (`--fred-ip`, `--relay-port`, …) |

**Running without a screen:** SSH in (over ethernet, or join `FrED_Hub` and
`ssh <user>@192.168.50.1`), then `bash start_hub.sh`. To keep it running after
you log out, start it inside `tmux` (`sudo apt install tmux`, `tmux`, run it,
detach with Ctrl+B then D, reattach with `tmux attach`).

**Turning the hub off:** just shut the Pi down. FrED keeps looking for the
hub; to use FrED without the hub, run `bash setup_wifi.sh hotspot` on FrED.

---

## 9. What changes on FrED and on the laptop

**FrED (Pi 4)** — `git pull`, then once `bash setup_wifi.sh hub`:

- `setup_wifi.sh` (new): `hub` joins `FrED_Hub` with the fixed address
  `192.168.50.2`, **WiFi power saving off** (as a client it would delay
  incoming messages), gateway/DNS = the hub, and switches FrED's own hotspot
  off; `hotspot` switches back; `status` shows the mode. Remembered across
  reboots.
- The screen shows the **WiFi mode** and whether the laptop is connected
  directly or through the relay, plus the new **Pi load (CPU)** box.
- `main.py` answers the hub's status ping (UDP 5006, one tiny packet every
  2 s).

**Laptop** — update the app, then:

- Join **`FrED_Hub`** instead of `FrED_Pi` (same password).
- New **Network** box: *Hub - direct to FrED* (default), *Hub - relay through
  the hub*, *FrED hotspot (old way)* — it fills in the address.
- The status shows the **round trip**; *Run info* records the network, the
  round trip and FrED's CPU during the recording.
- *Optional:* let the hub ping the laptop — PowerShell **as administrator**:
  `netsh advfirewall firewall add rule name="FrED hub ping" protocol=icmpv4:8,any dir=in action=allow`
- *Optional:* turn off the WiFi adapter's power saving (laptop README,
  section 6).

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `FrED_Hub` does not appear on the laptop | `bash hub_hotspot.sh status`. If DOWN: `bash hub_hotspot.sh` and read the error. Check `WIFI_COUNTRY` is set, `rfkill list` shows WiFi not blocked |
| Hotspot fails to start (`Connection activation failed`) | reboot the Pi 5 and run `bash hub_hotspot.sh` again; try `BAND="bg"` with `CHANNEL=""`; make sure no other program (e.g. a wpa_supplicant config) holds `wlan0` |
| Lost the SSH session during install | expected: the WiFi became the hotspot. Join `FrED_Hub` and `ssh <user>@192.168.50.1`, or use ethernet |
| FrED: *not on the hub WiFi (yet)* | on FrED: `bash setup_wifi.sh status`; if it never ran: `bash setup_wifi.sh hub`. Check the name/password match `hub.conf` |
| FrED answers ping but *program: no answer* | `main.py` is not running (`bash start_fred.sh`), or FrED runs older code without the status port (`git pull` on FrED) |
| Laptop *lost 100%* ping | Windows firewall blocks ping (section 9); harmless |
| Laptop: *Hub relay* message box | read it: FrED unreachable, relay busy, or another laptop connected directly |
| Laptop can't connect in *Hub - relay* | `start_hub.sh` must be running; the dashboard's RELAY line must say *idle* (not *not listening*) |
| RELAY: *not listening … is another hub.py running?* | another `hub.py` already holds port 5005: close it (`pkill -f hub.py`) |
| Weak signal / high or jumpy ping | move the Pis closer, keep metal away from the antennas (the Pi's antenna is at the board edge), try 5 GHz (`BAND="a"`) |
| `Hub` line shows *UNDER-VOLTAGE* | use a proper Pi 5 power supply |
| No internet on FrED/laptop through the hub | the hub needs an ethernet cable with internet; check `ip route` on the hub |

---

## 11. Files

| File | Role |
|---|---|
| `hub.py` | dashboard + relay + pings + FrED status pings (standard library only) |
| `hub.conf` | every network setting |
| `setup_install.sh` | first-time installer |
| `hub_hotspot.sh` | the `FrED_Hub` hotspot: up / down / status |
| `start_hub.sh` | app runner: checks the hotspot, then runs `hub.py` |

---

## 12. Status

Tested on Windows with the real code of all three machines over localhost:
FrED's real link server, this relay (simulated radio, real sockets) and the
laptop's real link — 30/30 sync pings and a 2.7 MB data retrieval through the
relay, intact; the relay refusing a second laptop, refusing while another
laptop is connected directly, and reporting FrED down; a silent laptop closed
after the timeout and FrED freed; FrED stopping detected on both sides; the
dashboard drawn through a stand-in terminal at 80 and 100 columns; parsers
for `iw`, `ip neigh` and the DHCP leases checked against sample output.

**Not yet verified on the real hardware:** the hotspot on the Pi 5
(NetworkManager), FrED joining it, `iw` station data, and the CPU savings on
FrED.
