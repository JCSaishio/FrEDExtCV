# $${\color{red}MIT}$$ $${\color{red}FrED}$$ — External CV variant, **WiFi** (Raspberry Pi 4)

Raspberry Pi code for the Fiber Extrusion Device, modified so that the fiber
**diameter is measured on an external computer** (the laptop CV app). This
folder is a **stand-alone replacement** for the original `fred-device` code:
copy it onto the Raspberry Pi 4, run the installer, and the machine works with
no camera attached to the Pi.

> **v7 — the Pi does control only.** The diameter is no longer streamed to
> the Pi at all: the laptop measures, graphs and records it at the full camera
> rate. The Pi runs the heater / stepper / spooler / fan control loops and
> graphs **temperature and spooler speed**. It still runs its own WiFi hotspot
> so the laptop can connect, send experiments and retrieve the data — once an
> experiment is sent, that link is essentially dormant (a few short commands
> and a tiny clock-sync ping every 2 s).

This version installs into a Python virtual environment (`fred-venv`) on a
Raspberry Pi 4. The main program is **`main.py`**.

---

## Quick start (Raspberry Pi 4)

```bash
# 1. Open a terminal inside this folder (the repo is cloned on the Pi):
cd fred-device-extcv-pi4v6

# 2. Install everything (only on a fresh Pi):
bash setup_install.sh

# 3. Turn the Pi into a WiFi hotspot the laptop can join (once per boot):
bash setup_hotspot.sh

# 4. Run the program (activates fred-venv and runs main.py):
bash start_fred.sh
```

To update an existing Pi to the latest code: `git pull` in the repo folder,
then restart the program (no reinstall needed).

The hotspot it creates is:

| | |
|---|---|
| **Wi-Fi name (SSID)** | `FrED_Pi` |
| **Password** | `fredfiber123` |
| **Pi address** | `192.168.4.1` (port `5005`) |

These details are also shown live in the **Laptop Link (WiFi)** panel of the Pi
interface.

### Running it manually with the venv

```bash
source fred-venv/bin/activate     # activate the virtual environment
python main.py                    # run the main program
deactivate                        # (optional) leave the venv when done
```

---

## Required libraries

### Installed from `apt` (system packages, shared into the venv)

These are made visible to `fred-venv` because the venv is created with
`--system-site-packages`. They are **not** pip-installed, because building
PyQt5 from pip on the Pi is slow and frequently ships **without the QtSvg
module** (the `cannot import 'QtSvg' from 'PyQt5'` error).

| apt package | Provides | Why |
|---|---|---|
| `python3-pyqt5` | PyQt5 GUI toolkit | the whole user interface |
| `python3-pyqt5.qtsvg` | PyQt5 **QtSvg** module | required by matplotlib's Qt5 backend |
| `python3-rpi.gpio` | `RPi.GPIO` | GPIO pin control (fan, extruder, spooler) |
| `libatlas-base-dev` | BLAS runtime | numpy / matplotlib link against it |
| `fonts-dejavu` | fonts | so matplotlib renders text labels |
| `python3-venv`, `python3-pip`, `python3-dev` | venv + pip + headers | to build the environment |

### Installed by `pip` into `fred-venv` (see `requirements.txt`)

| pip package | Import name | Used for |
|---|---|---|
| `PyYAML` | `yaml` | reads `calibration.yaml` |
| `numpy` | `numpy` | spooler math |
| `matplotlib` | `matplotlib` | live plots embedded in the Qt UI |
| `adafruit-blinka` | `board`, `busio`, `digitalio` | CircuitPython hardware layer (SPI/pins) |
| `adafruit-circuitpython-mcp3xxx` | `adafruit_mcp3xxx` | MCP3008 ADC for thermistor reads |
| `spidev` | `spidev` | SPI for the spooler encoder/DAC |

### Python standard library (no install needed)

`threading`, `time`, `math`, `sys`, `socket` (the WiFi link), `subprocess`,
`json`, `uuid`, `typing`, `csv`, `collections` — all bundled with Python 3.

---

## What changed vs. the original `fred-device`

- **No camera and no diameter on the Pi.** The original code opened
  `cv2.VideoCapture(0)` at start-up. Here the fiber is measured on the laptop,
  which also graphs and records the diameter (v7). Every Pi subsystem (heater,
  stepper/extruder, DC spooling motor, fans) runs without it.
- **No camera UI.** The image panes and image-processing controls were removed
  — those live on the laptop.
- **Layout.** Two large graphs on the left — **DC Motor** (spooler RPM) and
  **Temperature** — with every control grouped into panels on the right.

## Per-subsystem STOP buttons

Each actuator has its own red **STOP** button so you can halt it without
closing the program. Stopping drives that output to **0** immediately:

| Button | Where | Effect |
|---|---|---|
| **STOP Heater** | Extruder Heater panel | turns heating off (open- and closed-loop) and clears the PID state |
| **STOP Spooling Motor** | Spooling DC Motor panel | stops the spooler (open- and closed-loop) and clears its PID state |
| **STOP Stepper** | Extrusion Motor panel | sets the extrusion speed to 0 and zeroes the stepper output |
| **STOP / Start Fan** | Cooling Fan panel | holds the fan at 0% (toggles back on without losing the slider value) |

Restart heater/motor with their Start buttons; restart the stepper by raising
**Extrusion Motor Speed**.

## Spooler PID gain limits

The spooling-motor gains accept **Kp 0–1, Ki 0–15, Kd 0–0.05** (step 0.001
for Kd). Gains sent in an experiment from the laptop are held to the same
limits (`UserInterface.MOTOR_GAIN_LIMITS`).

## Passive Monitoring (read-only)

**Start Monitoring (no output)** graphs the **heater temperature** and
**spooler RPM** while driving **no output** — no heating, no motors. Starting
a control loop is blocked while monitoring is on; stop monitoring first.

## Graphs & sampling rate

**Reset Graphs** clears the on-screen **Temperature** and **DC Motor** plots
(logged data is kept). Plots are redrawn on the GUI thread ~10×/s and at most
1500 points per line are drawn (long runs are thinned for display only — every
sample is still recorded), so drawing never takes CPU from the control loops.

**Sampling rate (Hz)** (1–100, default 50) is the rate of the temperature and
spooler **control loops and of the recorded data — one rate for both** (v7).
An experiment sent from the laptop sets its own rate. The read-out below it
shows the rate actually achieved:

```
Target 50 Hz  |  recorded to CSV: temp 47 Hz, spooler 47 Hz  |  loop 480 Hz
```

Green = keeping up, orange = falling short (lower the rate), grey = idle. With
the 2 ms loop poll, a 50 Hz setting achieves ~46–48 Hz; every row carries its
exact timestamp.

### Clean measurements at a high rate (v7)

Running the loops faster used to make the signals noisier, which is why v6
dropped control back to 10 Hz. v7 keeps the high rate but measures over **fixed
time windows**, independent of the rate:

- **Spooler speed** = encoder counts over the last **0.1 s**
  (`Spooler.RPM_WINDOW`) instead of over one sample period. At 50 Hz a single
  period holds 5× fewer counts, so the RPM (and the PID built on it) was 5×
  more quantised; the window keeps it as clean as the original 10 Hz reading.
- **Temperature** = mean of the thermistor readings of the last **1 s**
  (`Thermistor.AVERAGE_WINDOW`) instead of the last 10 readings (which is only
  0.2 s at 50 Hz).

At 10 Hz both are **exactly** the original calculations (verified: 0.0
difference). At 50 Hz, in simulation, speed jitter drops from 0.34 to 0.15 RPM
and temperature jitter from 0.09 to 0.04 °C compared with v6's per-sample
maths. The PID gains and structure are unchanged. **Verify on the real
machine** that the spooler is stable at your chosen rate; setting 10 Hz gives
the original behaviour.

The Pi clock (`UserInterface.now()`) is monotonic, so a system-clock change
(e.g. NTP after boot) can never make the control loops jump.

## Remote experiments

The laptop app's **Experiment (FrED)** tab configures a run; this code runs it:

1. **HEATING** — heater only, for the *heating time*.
2. **HEATING + EXTRUSION** — heater and stepper (at its own priming rate), for
   the *heating + extrusion time*. Spooler and fan stay off.
3. **SETTLE** — spooler, stepper and fan all on, for the *settle time*.
4. **RECORDING** — everything runs **and** one data row is logged per sample
   tick, for the *data-taking time*. Temperature and spooler data are logged
   on FrED's clock with t = 0 at the start of recording.
5. **EXTRA SPOOLING** — heater, stepper and fan stop; the **spooler keeps
   running** for the *extra spooling time*. The data is already retrievable.
6. **COMPLETE** — everything stopped; the data waits for **Retrieve Data**.

**START RECORDING NOW** (laptop button, v7): the operator presses it when the
fiber drops. During heating / extrusion / settle the run jumps straight to
**RECORDING** (all systems on, t = 0 is that instant) for the full data-taking
time. With no run active, the laptop's run starts directly in RECORDING.

**One tick for control and data (v7).** In an experiment the temperature PID,
spooler PID, stepper, fan and the logged row all run on the same tick at the
experiment's sample rate; the row is written right after that tick's control
updates. `Temp new reading` / `Spooler new reading` (1 = fresh sensor reading,
0 = repeated value) document this row by row — in practice every row is fresh.

**Diameter:** the Pi's table has no diameter columns. It sends its recording
start/end times (Pi clock) with the data, and the laptop merges its own
camera data onto the rows (see the laptop README, *Time synchronisation*).

While a run is active **every control on this screen is disabled** and shown
in lighter gray, except the red STOP buttons. The manual control loops are
switched off when a run starts, so none of them resumes after the run.

**Graphs reset with the recording** — cleared when the experiment is received
and again when RECORDING starts, so they show exactly the exported window.

**Aborting stops everything** — a red **STOP** on the Pi or **ABORT** on the
laptop stops heater, stepper, spooler and fan (also during extra spooling) and
clears the manual control loops. With no run active, the laptop's Abort is a
remote all-stop. Implemented in `experiment.py`, driven by `main.py`.

## How the laptop link works

`laptop_link.py` is a TCP **server** on port **5005**; the laptop connects as
a client. Messages are newline-delimited JSON with a `type`:

| laptop → Pi | meaning |
|---|---|
| `{"type": "experiment", "params": {...}}` | start an automated run |
| `{"type": "start_now", "params": {...}}` | START RECORDING NOW |
| `{"type": "abort"}` | stop every system |
| `{"type": "get_data"}` | send the recorded table |
| `{"type": "sync", "id": n, "t1": t}` | clock-sync ping (every 2 s) |

| Pi → laptop | meaning |
|---|---|
| `{"type": "status", "phase", "remaining", "message", "data_ready", "t0"?}` | phase changes (`t0` = recording start, Pi clock) |
| `{"type": "data", "name", "b64", "meta"}` | recorded table (CSV, base64) + its t0 / t_end / rate |
| `{"type": "sync_reply", "id", "t1", "t2", "t3", "boot"}` | ping answer: arrival and departure times on the Pi clock |

The Pi answers sync pings on the network thread straight away. On every
(re)connection it sends the current phase, so the laptop stays in step after a
dropped link. The IP address shown on screen is cached for 10 s (reading it
spawns a process). Diameter lines from an older laptop app are ignored. The Pi
goes back to *waiting for laptop* if the connection drops.

## WiFi hotspot setup (`setup_hotspot.sh`)

So the link works **without any university/router network**, the Pi creates its
own WiFi access point. Run once (re-run after a reboot if it doesn't auto-start):

```bash
bash setup_hotspot.sh          # create + start the hotspot
bash setup_hotspot.sh status   # show the hotspot state and the Pi's IP
bash setup_hotspot.sh down     # stop it and return to your normal WiFi
```

It uses **NetworkManager (`nmcli`)** — the default on Raspberry Pi OS Bookworm —
to bring up an access point with a fixed address and a small DHCP server for the
laptop:

| | |
|---|---|
| **SSID** | `FrED_Pi` |
| **Password** | `fredfiber123` |
| **Pi IP** | `192.168.4.1` (the laptop connects here, port `5005`) |

These values live in `laptop_link.py` (the `HOTSPOT_*` / `LINK_PORT`
constants) and in `setup_hotspot.sh`; keep them in sync if you change them.
The Pi interface reads its **actual** address at runtime and displays it.

> While the Pi is a hotspot its WiFi is used for the access point and is **not**
> connected to the internet — that is intended. If you need a different
> `nmcli`-less setup (older Raspberry Pi OS), the script prints guidance for
> switching to NetworkManager or using `hostapd` + `dnsmasq`.

---

## Enabling the Pi hardware interfaces (one time)

The extruder ADC (MCP3008) and the spooler use **SPI**. Make sure it is enabled:

```bash
sudo raspi-config      # Interface Options -> SPI -> Enable, then reboot
```

---

## Microstepping (reducing stepper vibration)

The extrusion stepper is driven by a **DRV8825**. By default it ran in
**full-step** mode, which vibrates and disturbs the fiber. `extruder.py`
enables **1/16 microstepping** for much smoother motion, and scales the step
frequency by 16 so your **RPM setting is unchanged**.

On this PCB only the driver's **M2** mode pin is wired to a GPIO; **M0** and
**M1** are left floating and the DRV8825's internal pull-downs hold them LOW.
With `M0 = M1 = LOW`:

| M2 | Microstep mode |
|----|----------------|
| LOW  | full step (vibrates) |
| HIGH | **1/16 step (smooth)** |

The code drives M2 HIGH at start-up. On this PCB **M2** is wired to **BCM
GPIO22** (physical header pin 15), confirmed by a continuity test
(`MICROSTEP_M2_PIN = 22` in `extruder.py`). If you move to a different board,
re-confirm the pin with a multimeter (continuity between the DRV8825 **M2** pad
and the header). A wrong pin makes the motor vibrate *and* turn ~16× too slow.
To go back to full step, set `MICROSTEP_FACTOR = 1`.

---

## Troubleshooting

- **Installer fails to download anything** (`Release file ... is not valid
  yet`, `certificate is not yet valid`) — the Pi's clock is wrong. The
  installer fixes this as its **first step** (NTP, or the date from a web
  server's HTTP header). If it still can't, the Pi has no internet route —
  the `FrED_Pi` hotspot has **no internet**: `bash setup_hotspot.sh down`,
  join a normal WiFi (or plug in ethernet), re-run `bash setup_install.sh`.
- **`cannot import 'QtSvg' from 'PyQt5'`** — `sudo apt install python3-pyqt5
  python3-pyqt5.qtsvg`, and make sure `fred-venv` was created with
  `--system-site-packages` (delete it and re-run `setup_install.sh` if not).
- **GUI doesn't appear / `qt.qpa.plugin` errors** — run from the Pi's desktop
  session (or with `DISPLAY` set), not a bare SSH session.
- **Where is the diameter graph?** — on the laptop (v7). The Pi shows only
  temperature and spooler speed.
- **"Retrieve Data" waits forever on the laptop** — the recorded CSV travels as
  one large message; sends get their own 30 s deadline (`SEND_TIMEOUT` in
  `laptop_link.py`) and a failed send closes the connection so the laptop
  reconnects clean. Make sure the Pi runs the current code (`git pull`, then
  restart).
- **Laptop can't connect / "connection refused"** — confirm the hotspot is up
  (`bash setup_hotspot.sh status`), that the laptop joined `FrED_Pi`, and that
  the Pi program is running (it opens port 5005).
- **Spooler oscillates at a high sampling rate** — lower the experiment's
  *FrED sample rate* (10 Hz = original behaviour) and report it; see *Clean
  measurements at a high rate* above.
- **Re-running the installer** is safe: it reuses `fred-venv` and only installs
  what's missing.

---

## Run summary

```bash
bash setup_install.sh     # once, fresh Pi only
bash setup_hotspot.sh     # once per boot
bash start_fred.sh        # every time (or: source fred-venv/bin/activate; python main.py)
```

Then, on the laptop, join the `FrED_Pi` Wi-Fi, run *FrED Fiber Measure*,
enter the Pi's IP/port (default `192.168.4.1` : `5005`) and click **Connect**.

## Modules

- `main.py` — **entry point**; starts the GUI and the hardware-control thread.
- `user_interface.py` — PyQt5 interface (grouped controls + 2 graphs); shows
  the WiFi connection details; owns the Pi clock (`now()`).
- `experiment.py` — remote-experiment state machine (phases, START NOW,
  one-tick control + logging, recorded table).
- `laptop_link.py` — TCP **server** for the laptop's commands and clock-sync
  pings (replaces `external_diameter.py`).
- `setup_hotspot.sh` — turns the Pi into a self-contained WiFi access point.
- `database.py` — data storage and the manual CSV export.
- `extruder.py` — heater + stepper control (thermistor, PID).
- `spooler.py` — DC spooling motor control (encoder, PID, calibration).
- `fan.py` — cooling-fan control.
- `fake_gpio.py` — RPi.GPIO stand-in for off-Pi testing.
- `calibration.yaml` — motor calibration.
- `setup_install.sh` — installer (apt packages + `fred-venv` + `requirements.txt`).
- `start_fred.sh` — activates `fred-venv` and runs `main.py`.
- `requirements.txt` — pip packages installed into the venv.
- `Moving_AVG_TEMP.py` — standalone temperature-test utility (not used by `main.py`).
