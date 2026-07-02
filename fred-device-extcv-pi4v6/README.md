# $${\color{red}MIT}$$ $${\color{red}FrED}$$ — External CV variant, **WiFi** (Raspberry Pi 4)

Raspberry Pi code for the Fiber Extrusion Device, modified so that the fiber
**diameter is measured on an external computer** and streamed to the Pi **over
WiFi** (no USB cable). This folder is a **stand-alone replacement** for the
original `fred-device` code: copy it onto the Raspberry Pi 4, run the installer,
and the machine works with no camera attached to the Pi.

> **v3 — wireless link.** Earlier versions received the diameter over a USB
> serial cable. In this version the **Pi runs as its own WiFi hotspot**, the
> laptop joins it, and the diameter is streamed over a TCP socket. The
> connection details (Wi-Fi name/password, the Pi's IP and port) are shown
> right on the Pi's screen so you can connect from the laptop with no guesswork.

This version is packaged to **install cleanly into a Python virtual environment
(`fred-venv`) on a Raspberry Pi 4**. The main program is **`main.py`**.

---

## Quick start (Raspberry Pi 4)

```bash
# 1. Copy this whole folder onto the Pi, then open a terminal inside it:
cd "fred-device-external-cv-pi4v3"

# 2. Install everything (apt system packages + fred-venv + pip packages):
bash setup_install.sh

# 3. Turn the Pi into a WiFi hotspot the laptop can join (one time / per boot):
bash setup_hotspot.sh

# 4. Run the program (this activates fred-venv and runs main.py):
bash start_fred.sh
```

The hotspot it creates is:

| | |
|---|---|
| **Wi-Fi name (SSID)** | `FrED_Pi` |
| **Password** | `fredfiber123` |
| **Pi address** | `192.168.4.1` (port `5005`) |

These exact details are also shown live in the **Diameter (External CV - WiFi)**
panel of the Pi interface, so you never have to look them up.

That's it. The installer creates the `fred-venv` virtual environment **inside
this folder**, installs every required library, and verifies that they all
import before finishing.

### Running it manually with the venv

If you prefer to do it by hand (the program always runs from inside the venv):

```bash
source fred-venv/bin/activate     # activate the virtual environment
python main.py                    # run the main program
deactivate                        # (optional) leave the venv when done
```

> The activation command is exactly **`source fred-venv/bin/activate`**, run
> from inside this folder. Once activated your prompt shows `(fred-venv)`.

---

## Required libraries

Everything the program imports, and how it gets installed on the Pi 4.

### Installed from `apt` (system packages, shared into the venv)

These are built for the Pi by `apt` and made visible to `fred-venv` because the
venv is created with `--system-site-packages`. They are **not** pip-installed,
because building PyQt5 from pip on the Pi is slow and frequently ships **without
the QtSvg module** (the `cannot import 'QtSvg' from 'PyQt5'` error).

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
`json`, `typing`, `csv`, `collections` — all bundled with Python 3.

---

## What changed vs. the original `fred-device`

- **No camera dependency.** The original code opened `cv2.VideoCapture(0)` at
  start-up and the interface would not run without a camera. That dependency is
  gone — every other subsystem (heater, stepper/extruder, DC spooling motor,
  fans) runs with no camera and no diameter stream connected.
- **Diameter comes over WiFi.** A separate program on your computer
  (*FrED Fiber Measure with Streaming v3*) measures the fiber from a camera
  connected to the computer and streams the diameter to the Pi over a wireless
  TCP socket. The interface treats those values exactly like the old camera
  readings (same plot, same `Database` buffers, same CSV export).
- **No camera UI.** The raw/processed image panes and all the image-processing
  controls (erode / dilate / Gaussian / binary, Canny and Hough sliders, camera
  calibration) were removed — those now live on the computer.
- **"Start Diameter/Camera Loop" button kept.** Press it to begin graphing the
  streamed diameter on the Pi (it toggles `diameter_loop_enabled`).
- **Redesigned layout.** The space the camera feed used to occupy now holds
  larger **Diameter**, **DC Motor** and **Temperature** graphs on the left, with
  all controls and the CSV export grouped into panels on the right.

## Per-subsystem STOP buttons

Each actuator now has its own red **STOP** button so you can halt it without
closing the program. Stopping drives that output to **0** immediately (it isn't
left at its last value):

| Button | Where | Effect |
|---|---|---|
| **STOP Heater** | Extruder Heater panel | turns heating off (open- and closed-loop) and clears the PID state |
| **STOP Spooling Motor** | Spooling DC Motor panel | stops the spooler (open- and closed-loop) and clears its PID state |
| **STOP Stepper** | Extrusion Motor panel | sets the extrusion speed to 0 and zeroes the stepper output |
| **STOP / Start Fan** | Cooling Fan panel | holds the fan at 0% (toggles back on without losing the slider value) |

Restart heater/motor with their existing Start buttons; restart the stepper by
raising **Extrusion Motor Speed**.

## Passive Monitoring (read-only)

The **Passive Monitoring** panel has a **Start Monitoring (no output)** button
that graphs the **heater temperature** and **spooler RPM** while driving **no
output** to the system — no heating, no motors. It's meant for watching how the
heater temperature behaves on its own (its current value and trend) with no
control input. Starting any control loop is blocked while monitoring is on; stop
monitoring first.

## Graphs: Reset button & sampling rate (v5)

**Reset Graphs button** — the **Graphs** panel has a **Reset Graphs** button that
clears the on-screen **Diameter**, **Temperature** and **DC Motor** plots so a
new run can be seen cleanly. It only clears what is drawn; data already logged
for the CSV export is kept.

**Adjustable sampling rate + live read-out** — the **Graphs & Sampling** panel
has a **Sampling rate (Hz)** box (1–100 Hz, default 50). The temperature and
spooler control loops read this **live**, so you can change the rate without
restarting. Below it, a line shows the rate **actually being written to the CSV
buffers**:

```
Target 50 Hz  |  recorded to CSV: temp 50 Hz, spooler 0 Hz  |  loop 480 Hz
```

It turns **green** when the recorded rate keeps up with the target, **orange**
if it's falling short (lower the target then), and grey when nothing is
recording. This directly answers "am I really getting my samples into the CSV?"

> **Important fix in v5:** earlier versions redrew the plots *inside the
> hardware-control thread*. On the Pi each matplotlib redraw costs tens of ms,
> so the redraws starved the sampling loop and only ~4 rows/s reached the CSV —
> far below the requested rate. v5 makes the hardware thread **append-only**
> (microseconds) and moves all redrawing to a **QTimer on the GUI thread**
> (`Plot.redraw`, ~10 FPS). Sampling now runs at the full selected rate and
> every sample lands in the CSV.

| | v4 and earlier | v5 |
|---|---|---|
| Temperature loop | 0.1 s — 10 Hz | **0.02 s — 50 Hz** (live-adjustable) |
| Spooler loop | 0.1 s — 10 Hz | **0.02 s — 50 Hz** (live-adjustable) |
| Hardware loop poll (`main.LOOP_SLEEP`) | 0.05 s — 20 Hz | **0.002 s — ~500 Hz** |
| Plot redraw | in the hardware thread | **GUI thread, ~10 FPS** |

### How fast can we actually go?

The sensors are **not** the bottleneck:
- **Thermistor (MCP3008 ADC over SPI):** a conversion is well under 1 ms; the
  chip can do tens of kHz. Temperature also changes slowly, so oversampling +
  the moving average just makes the trace smoother.
- **Spooler encoder (SPI read):** also sub-millisecond.

With drawing moved off the sampling loop, the remaining limits are:
1. **RPM quantisation at low speed** — RPM is `encoder_delta / 4704 × (60/dt)`.
   The shorter `dt` is, the fewer counts per sample, so at **low RPM** the
   reading (and the PID derivative built from it) gets noisier.
2. **Python loop + Linux sleep granularity** (~1 ms) and the PID derivative
   amplifying sensor noise as `dt` shrinks.

**Recommendation:** 50 Hz is a comfortable, stable 5× increase and the default.
You can dial it up toward **100 Hz** from the interface for denser data, but
expect noisier RPM at low spool speeds; past ~100 Hz you gain little real
information and add noise. **Use the live read-out to decide:** if "recorded to
CSV" can't keep up with the target (stays orange), back the rate off. The tuning
constants are `main.LOOP_SLEEP` and `Plot.REDRAW_INTERVAL_MS`; the per-loop rate
is the on-screen spinbox.

## Remote experiments (v6)

The laptop can send a **whole experiment** to FrED and have it run automatically,
then send the recorded data back. Configure it on the laptop app's
**Experiment (FrED)** tab; this Pi code runs the sequence:

1. **HEATING** — heater only, for the *heating time*.
2. **HEATING + EXTRUSION** — heater **and** the extrusion stepper, for the
   *heating + extrusion time*. The stepper runs at its **own, independently
   configurable rate** for this phase (set on the laptop), so the extruder can
   be primed before anything spools. Spooler and fan stay off.
3. **SETTLE** — spooler, stepper (now at the experiment's normal extrusion
   speed) and fan all activated, wait the *experiment settle time*.
4. **RECORDING** — everything runs **and** data is recorded, for the
   *data-taking time*. Diameter (streamed from the laptop), temperature and
   spooler RPM are all logged on **FrED's own clock**, rebased so t = 0 at the
   start of recording.
5. **EXTRA SPOOLING** — heater, stepper and fan stop, but the **spooler keeps
   running** for the user-set *extra spooling time*, coiling the fiber that was
   already extruded. The recorded CSV is already available to retrieve during
   this phase.
6. **COMPLETE** — every actuator stopped; the CSV is held until the laptop
   clicks **Retrieve Data**.

Heater and spooler each run **closed-loop** (setpoint + PID) or **open-loop**
(raw PWM), chosen per run on the laptop. While an experiment runs, **every
on-screen control is disabled** — start buttons, PID gain boxes, setpoint
spinboxes, the temperature and fan sliders, the sampling rate, graph reset and
CSV export — and shown in lighter gray colors, so nothing can interfere with
the run. The only live controls are the **red STOP buttons, which abort the
run** and stop everything (including the extra-spooling phase). The current
phase and time remaining show in the Diameter panel. Implemented in
`experiment.py` (state machine), driven by `main.py`; the recorded data is
sent back over the same WiFi link.

## How the link works

The **Pi is the server**: `external_diameter.py` listens on TCP port **5005**
and accepts a connection from the laptop. The laptop sends newline-delimited
JSON, one message per measurement:

```json
{"v": 1, "d": 0.352, "u": "mm", "t": 12.345, "found": true}
```

| field | meaning |
|---|---|
| `v` | protocol version (1) |
| `d` | diameter value, in the units of `u` |
| `u` | units (`mm`, or `px` if the computer is not calibrated) |
| `t` | sender elapsed seconds (informational) |
| `found` | whether a fiber was detected in that frame |

`external_diameter.py` reads this on a background thread and exposes the latest
value. The Pi keeps the port open and automatically goes back to *waiting for a
laptop* if the connection drops, so the programs can be started in any order and
the laptop can disconnect/reconnect at any time. Calibrate the camera **on the
computer** so the streamed `d` is already in millimetres (matching the Target
Diameter range of 0.30–0.60 mm).

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

These values live in `external_diameter.py` (the `HOTSPOT_*` / `STREAM_PORT`
constants) and in `setup_hotspot.sh`; keep them in sync if you change them. The
Pi interface reads its **actual** address at runtime and displays it, so even if
it differs you can always read the right IP off the screen.

> While the Pi is a hotspot its WiFi is used for the access point and is **not**
> connected to the internet — that is intended, and is what makes the link
> self-contained and reliable. If you need a different `nmcli`-less setup
> (older Raspberry Pi OS), the script prints guidance for switching to
> NetworkManager or using `hostapd` + `dnsmasq`.

---

## Enabling the Pi hardware interfaces (one time)

The extruder ADC (MCP3008) and the spooler use **SPI**. Make sure it is enabled:

```bash
sudo raspi-config      # Interface Options -> SPI -> Enable, then reboot
```

(The diameter link is now over WiFi, so no serial/UART configuration is needed.)

---

## Microstepping (reducing stepper vibration)

The extrusion stepper is driven by a **DRV8825**. By default it ran in
**full-step** mode, which vibrates and disturbs the fiber. `extruder.py` now
enables **1/16 microstepping** for much smoother motion, and scales the step
frequency by 16 so your **RPM setting is unchanged**.

On this PCB only the driver's **M2** mode pin is wired to a GPIO; **M0** and
**M1** are left floating and the DRV8825's internal pull-downs hold them LOW.
With `M0 = M1 = LOW`:

| M2 | Microstep mode |
|----|----------------|
| LOW  | full step (vibrates) |
| HIGH | **1/16 step (smooth)** |

The code drives M2 HIGH at start-up.

### M2 pin number

On this PCB the DRV8825 **M2** pin is wired to **BCM GPIO22** (physical header
pin 15), confirmed by a continuity test:

```python
MICROSTEP_M2_PIN = 22   # in extruder.py
```

If you ever move it to a different board, re-confirm the pin (BCM 16 is the
stepper **direction** pin, so M2 is never 16). If the value is wrong the motor
still vibrates *and* spins at the wrong speed (because of the ×16 scaling). To
find it: power the Pi **off**, set a multimeter to continuity, touch one probe
to the DRV8825 **M2** pad and the other to header pins until it beeps — that
pin's **BCM** number is the value for `MICROSTEP_M2_PIN`.

To verify after setting it: run the extruder at a known RPM — it should sound
noticeably smoother/quieter, and turn at the same speed as before (not ~16×
slower). If it runs very slow, the pin is wrong.

To temporarily go back to full step, set `MICROSTEP_FACTOR = 1` and don't drive
M2 (or leave the wrong pin) — but microstepping is the recommended setting.

---

## Troubleshooting

- **`cannot import 'QtSvg' from 'PyQt5'`** — the QtSvg module is missing. The
  installer fixes this by installing `python3-pyqt5.qtsvg` from apt and building
  `fred-venv` with `--system-site-packages`. If you hit it manually:
  ```bash
  sudo apt install python3-pyqt5 python3-pyqt5.qtsvg
  ```
  and make sure your venv was created with `--system-site-packages` (delete and
  re-run `setup_install.sh` if it wasn't).
- **GUI doesn't appear / `qt.qpa.plugin` errors** — run from the Pi's desktop
  session (or with `DISPLAY` set), not a bare SSH session without X forwarding.
- **No diameter graph** — make sure the laptop is joined to the `FrED_Pi`
  Wi-Fi, then in *FrED Fiber Measure with Streaming v3* enter the Pi's IP/port,
  click **Connect** and **Start streaming**, and on the Pi press **Start
  Diameter/Camera Loop**.
- **Laptop can't connect / "connection refused"** — confirm the hotspot is up
  (`bash setup_hotspot.sh status`), that the laptop joined `FrED_Pi`, and that
  the Pi program is running (it's what opens port 5005). The Pi panel shows
  *waiting for laptop* until the laptop connects.
- **Re-running the installer** is safe: it reuses an existing `fred-venv` and
  only installs what's missing.

---

## Run summary

```bash
# install once
bash setup_install.sh

# start the WiFi hotspot (once, or after a reboot)
bash setup_hotspot.sh

# every time you want to run it:
source fred-venv/bin/activate
python main.py
# ...or the shortcut that does both:
bash start_fred.sh
```

Then, on your computer, join the `FrED_Pi` Wi-Fi, run *FrED Fiber Measure with
Streaming v3*, enter the Pi's IP/port (shown on the Pi screen, default
`192.168.4.1` : `5005`), click **Connect** and **Start streaming**. On the Pi,
press **Start Diameter/Camera Loop** to begin graphing.

## Modules

- `main.py` — **entry point**; starts the GUI and the hardware-control thread.
- `user_interface.py` — PyQt5 interface (grouped controls + 3 enlarged graphs);
  shows the live WiFi connection details for the laptop.
- `external_diameter.py` — TCP **server** that receives the streamed diameter
  over WiFi and feeds it into the diameter plot / `Database` (replaces
  `fiber_camera.py`).
- `setup_hotspot.sh` — turns the Pi into a self-contained WiFi access point.
- `database.py` — data storage and CSV generation.
- `extruder.py` — heater + stepper control (thermistor, PID).
- `spooler.py` — DC spooling motor control (encoder, PID, calibration).
- `fan.py` — cooling-fan control.
- `fake_gpio.py` — RPi.GPIO stand-in for off-Pi testing.
- `calibration.yaml` — motor calibration (diameter calibration now lives on the
  computer).
- `setup_install.sh` — installer (apt packages + `fred-venv` + `requirements.txt`).
- `start_fred.sh` — activates `fred-venv` and runs `main.py`.
- `requirements.txt` — pip packages installed into the venv.
- `Moving_AVG_TEMP.py` — standalone temperature-test utility (not used by `main.py`).
