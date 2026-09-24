# $${\color{red}MIT}$$ $${\color{red}FrED}$$ — External CV variant, **WiFi**, v7 (Raspberry Pi 4)

Raspberry Pi code for the Fiber Extrusion Device. The Pi runs the machine —
**heater, extrusion stepper, DC spooling motor and fan** — and executes
automated experiments sent from the laptop. The fiber **diameter is measured,
graphed and recorded on the laptop** (see
[`../FrEDFiberMeasurewithStreamingv7/`](../FrEDFiberMeasurewithStreamingv7/)).

This folder is a stand-alone replacement for the original `fred-device` code.
It installs into a Python virtual environment (`fred-venv`); the program is
**`main.py`** (started by `start_fred.sh`). Both the Pi and the laptop must
run v7.

---

## Contents

1. [What the Pi does in v7](#1-what-the-pi-does-in-v7)
2. [Install, update and run](#2-install-update-and-run)
3. [Tour of the interface](#3-tour-of-the-interface)
4. [Control loops and sampling rate](#4-control-loops-and-sampling-rate)
5. [Manual operation: STOP buttons, monitoring, gain limits, CSV](#5-manual-operation-stop-buttons-monitoring-gain-limits-csv)
6. [Remote experiments](#6-remote-experiments)
7. [The laptop link (protocol)](#7-the-laptop-link-protocol)
8. [WiFi hotspot](#8-wifi-hotspot)
9. [Hardware notes: SPI and the stepper](#9-hardware-notes-spi-and-the-stepper)
10. [Other tools in this folder (not used by main.py)](#10-other-tools-in-this-folder-not-used-by-mainpy)
11. [Required libraries](#11-required-libraries)
12. [Modules and tuning constants](#12-modules-and-tuning-constants)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. What the Pi does in v7

Up to v6 the laptop streamed ~20 diameter messages per second to the Pi, which
parsed, filtered, stored and graphed them — CPU time taken from the heater and
spooler PID loops. **In v7 the Pi does control only:**

| Work | v6 (Pi) | v7 (Pi) |
|---|---|---|
| Heater / stepper / spooler / fan control | yes | yes |
| Graphs | Diameter, DC Motor, Temperature | **DC Motor, Temperature** (thinned to ≤ 1500 drawn points each) |
| Receive + filter + store the diameter | ~20 messages/s | **none** — the laptop keeps it |
| Network traffic during a run | diameter stream + commands | a few commands + **one tiny sync ping every 2 s** |
| Read its own IP address for the screen | a new process 4× per second | cached, at most every 10 s |
| Control rate vs. data rate | control 10 Hz, data rows 50 Hz (repeated values) | **one rate for both** (default 50 Hz), clean measurements via time windows |
| Clock | wall clock (could jump when the time is set) | **monotonic** experiment clock |

The WiFi hotspot and the TCP server stay, so the laptop can connect, send an
experiment, press START RECORDING NOW / ABORT, and retrieve the data.

---

## 2. Install, update and run

The repository is cloned on the Pi.

```bash
cd fred-device-extcv-pi4v7

bash setup_install.sh     # ONLY on a fresh Pi (apt packages + fred-venv + pip)
bash setup_hotspot.sh     # once per boot: start the FrED_Pi WiFi hotspot
bash start_fred.sh        # every time: activates fred-venv and runs main.py
```

**Update to the latest code:** `git pull` in the repository folder, then
restart the program. No reinstall is needed.

**First update after the v7 folder rename.** This folder was called
`fred-device-extcv-pi4v6` before v7. On a Pi that still has the old folder:

```bash
cd ~/FrEDExtCV                  # the repository folder (wherever it was cloned)
git pull                        # the code moves to fred-device-extcv-pi4v7/
cd fred-device-extcv-pi4v7
bash start_fred.sh
```

- `git pull` moves every file of the code, but the git-ignored `fred-venv`
  (the installed Python environment) stays in the old
  `fred-device-extcv-pi4v6/` folder — a venv cannot be moved, its paths are
  fixed when it is created. `start_fred.sh` finds it there automatically
  (it prints *Using the existing fred-venv from the old v6 folder*), so **no
  reinstall is needed**. Do **not** delete the old folder while it holds
  `fred-venv`. (Running `setup_install.sh` in the new folder would create a
  fresh `fred-venv` there, after which the old folder can be deleted — this
  needs internet, so not over the hotspot.)
- Anything else you saved in the old folder (e.g. CSVs from *Download CSV
  File*) also stays there.
- If `git pull` refuses because of local changes (e.g. `calibration.yaml`
  after a motor calibration), copy that file somewhere, run `git checkout --
  <file>`, pull, then copy it into the new folder.

Manual run with the venv:

```bash
source fred-venv/bin/activate     # prompt shows (fred-venv)
python main.py
deactivate                        # optional
```

The installer fixes the Pi's clock first (NTP, or the date from a web server's
HTTP header) because a wrong clock breaks apt/pip. It builds `fred-venv` with
`--system-site-packages` so the apt-installed PyQt5 (with QtSvg) is used, and
checks that every library imports before finishing. Re-running it is safe.

---

## 3. Tour of the interface

**Left:** two large graphs — **DC Spooling Motor** (spooler RPM and its
setpoint) and **Temperature** (the controlled temperature and its setpoint).
They are redrawn ~10× per second on the GUI thread; the control thread only
appends numbers, so drawing never slows the control loops.

**Right (scrollable panels, top to bottom):**

| Panel | Contents |
|---|---|
| **Laptop Link (WiFi)** | the Wi-Fi name, password, IP and port to type on the laptop; the experiment status (phase and time left); the link status (green = laptop connected, red = waiting) |
| **Graphs & Sampling** | Reset Graphs, the **Sampling rate (Hz)** box and the achieved-rate read-out (section 4) |
| **Passive Monitoring** | graph temperature and spooler RPM with no output driven (section 5) |
| **Extruder Heater** | temperature setpoint slider, Temp Kp / Ki / Kd, heater open-loop PWM, Start Temperature Close Loop, Start Heater Open Loop, **STOP Heater** |
| **Spooling DC Motor** | motor setpoint (RPM), Motor Kp / Ki / Kd (**limited to 1 / 15 / 0.05**), DC motor PWM, Start Motor Close Loop, Start DC Motor Open Loop, **STOP Spooling Motor** |
| **Extrusion Motor** | extrusion speed (RPM), **STOP Stepper** |
| **Cooling Fan** | duty slider, STOP / Start Fan |
| **Data Export** | file name + Download CSV File (manual CSV of everything logged) |

While an experiment runs, **every control is disabled** and shown in light
grey, **except the red STOP buttons**, which abort the run.

---

## 4. Control loops and sampling rate

### One rate for control and data

The **Sampling rate (Hz)** box (1–100, default **50**) sets how often the
temperature loop and the spooler loop run — in manual mode, in monitoring, and
in experiments. An experiment sent from the laptop brings its own rate
(*FrED sample rate* on the laptop), which overrides the box during the run.

During an experiment, **one tick** drives everything: the heater PID, the
spooler PID, the stepper, the fan and the recorded data row, in that order and
with the same timestamp. So control and data run at the **same frequency** and
every recorded row holds fresh sensor readings.

The loop that schedules the ticks polls every 2 ms, so a tick happens up to a
couple of milliseconds after it is due: a **50 Hz setting achieves about
46–48 Hz**. Every sample carries its exact timestamp, and the achieved rate is
shown on screen and in the laptop's *Run info* sheet.

### Clean measurements at a high rate (time windows)

Running the loops faster used to make the measured signals noisier — that is
why v6 dropped control back to 10 Hz. v7 keeps the high rate but measures each
signal over a **fixed time window**, independent of the rate:

- **Spooler speed** = encoder counts over the last **0.1 s**
  (`Spooler.RPM_WINDOW`). Before, it used one sample period: at 50 Hz that
  window is 5× shorter, holds 5× fewer counts, and the speed (and the PID
  built on it) is 5× more quantised.
- **Temperature for the PID** = mean of the thermistor readings of the last
  **1 s** (`Thermistor.AVERAGE_WINDOW`). Before, it was the mean of the last
  10 readings — 1 s at 10 Hz, but only 0.2 s at 50 Hz.

| | At 10 Hz | At 50 Hz (simulation) |
|---|---|---|
| Spooler speed | **exactly** the original calculation (0.0 difference) | jitter 0.15 RPM vs 0.34 RPM with v6's per-period calculation |
| Temperature | **exactly** the original 10-reading mean (0.0 difference) | jitter 0.04 °C vs 0.09 °C with v6's 10-reading mean |

The PID gains and structure are unchanged. **Setting 10 Hz reproduces the
original behaviour exactly.**

Other facts worth knowing:
- The heater's PWM itself runs at **1 Hz** (`GPIO.PWM(HEATER_PIN, 1)`), so the
  heater cannot respond faster than about once a second whatever the rate.
- The recorded `Temperature (C)` is the raw thermistor reading of that tick;
  the PID error and the graph use the 1 s mean.
- The PID derivative uses the change between consecutive ticks divided by the
  tick period, as before.

### Achieved-rate read-out

Below the Sampling rate box:

```
Target 50 Hz  |  recorded to CSV: temp 47 Hz, spooler 47 Hz  |  loop 480 Hz
```

**Green** = keeping up with the target, **orange** = falling short (lower the
rate), **grey** = nothing is running. *loop* is how often the control thread
polls.

### The clock

`UserInterface.now()` is a **monotonic** clock (seconds since the program
started). It never jumps when the system time changes (e.g. NTP correcting the
time after boot), so control loops cannot see a sudden huge or negative time
step. Every recorded sample and the clock-sync replies use this clock.

---

## 5. Manual operation: STOP buttons, monitoring, gain limits, CSV

### STOP buttons

Each actuator has its own red STOP button; stopping drives that output to
**0** immediately (not left at its last value):

| Button | Effect |
|---|---|
| **STOP Heater** | turns heating off (open- and closed-loop) and clears the PID state |
| **STOP Spooling Motor** | stops the spooler (open- and closed-loop) and clears its PID state |
| **STOP Stepper** | sets the extrusion speed to 0 and zeroes the stepper output |
| **STOP / Start Fan** | holds the fan at 0 % (toggles back on without losing the slider value) |

Restart heater / motor with their Start buttons; restart the stepper by
raising **Extrusion Motor Speed**. During an experiment any red STOP aborts
the whole run (section 6).

### Passive Monitoring (read-only)

**Start Monitoring (no output)** graphs the heater temperature and the spooler
RPM while driving **no output** — no heating, no motors — e.g. to watch how
the temperature behaves on its own. Starting a control loop is blocked while
monitoring is on.

### Spooler PID gain limits

The spooling-motor gain boxes accept **Kp 0–1, Ki 0–15, Kd 0–0.05** (Kd in
steps of 0.001). Gains arriving in an experiment from the laptop are clamped
to the same limits (`UserInterface.MOTOR_GAIN_LIMITS`), and the laptop refuses
to send values outside them.

### Manual CSV export

*Data Export* → type a name → **Download CSV File** writes `<name>.csv` in
this folder with three tables (TEMPERATURE / DIAMETER / MOTOR) of everything
logged since the program started. In v7 the DIAMETER table is empty — the
diameter lives on the laptop. For experiments use the laptop's Retrieve Data.

---

## 6. Remote experiments

The laptop's *Experiment (FrED)* tab configures a run; `experiment.py` runs
it.

### Phases

| Phase | Heater | Stepper | Spooler | Fan | Data rows | Ends after |
|---|---|---|---|---|---|---|
| **HEATING** | on (closed or open loop) | 0 | 0 | 0 | — | heating time |
| **HEATING + EXTRUSION** | on | priming rate | 0 | 0 | — | heating + extrusion time |
| **SETTLE** | on | extrusion speed | on (closed or open loop) | fan duty | — | settle time |
| **RECORDING** | on | extrusion speed | on | fan duty | **one per tick** | data-taking time |
| **EXTRA SPOOLING** | off | 0 | on | 0 | — (data ready) | extra spooling time |
| **COMPLETE** | off | 0 | 0 | 0 | held for the laptop | — |

### START RECORDING NOW (laptop button)

The operator presses it the moment the fiber drops:
- during HEATING, HEATING + EXTRUSION or SETTLE, the run **jumps straight to
  RECORDING**: all systems on, t = 0 is that instant, for the full
  data-taking time;
- with **no run active**, the laptop's run starts **directly in RECORDING**;
- during RECORDING or later it is ignored (the laptop is told).

The switch happens on the control thread at its next loop iteration (a few
milliseconds), and its exact time becomes t = 0.

### What is recorded

One row per tick during RECORDING, 18 columns:

`Time (s)`, `Temperature (C)`, `Temp setpoint (C)`, `Temp error (C)`,
`Temp PID output`, `Temp Kp`, `Temp Ki`, `Temp Kd`, **`Temp new reading`**,
`Diameter setpoint (mm)`, `Fan duty (%)`, `Extruder RPM`,
`Spooler setpoint (RPM)`, `Spooler RPM`, `Spooler Kp`, `Spooler Ki`,
`Spooler Kd`, **`Spooler new reading`**

- `Time (s)` is seconds since recording started (t = 0), on the Pi clock.
- `Temp new reading` / `Spooler new reading`: 1 = a fresh sensor reading in
  this row, 0 = the value is repeated from before (in practice 1 in every row,
  since control and logging share the tick).
- The table has **no diameter columns**: the Pi sends its recording start and
  end times (Pi clock) with the table, and the laptop merges its own camera
  data onto these rows (laptop README, *Time synchronisation*).
- Format: semicolon-delimited, comma decimals (Excel es-MX).

### Rules while a run is active

- Every control on this screen is disabled except the red STOP buttons.
- The manual control loops (heater close/open loop, motor close/open loop)
  are switched off when a run starts, so none of them resumes after the run.
- The graphs are cleared when the experiment arrives and again when RECORDING
  starts, so they show exactly the exported window.

### Abort

A red **STOP** on the Pi or **ABORT** on the laptop stops **all** systems —
heater, stepper, spooler and fan are driven to zero (also during extra
spooling) — and clears the manual loops. With no run active, the laptop's
Abort is a remote all-stop.

---

## 7. The laptop link (protocol)

`laptop_link.py` is a TCP **server** on port **5005**; the laptop connects as a
client. Messages are one JSON object per line, each with a `type`.

**Laptop → Pi**

| Message | Meaning |
|---|---|
| `{"type": "experiment", "params": {...}}` | start an automated run (all settings of the Experiment tab) |
| `{"type": "start_now", "params": {...}}` | START RECORDING NOW |
| `{"type": "abort"}` | stop every system |
| `{"type": "get_data"}` | send the recorded table |
| `{"type": "sync", "id": n, "t1": t}` | clock-sync ping (every 2 s) |

`params` keys: `name`, `heater_mode`, `target_temperature`, `temp_kp`,
`temp_ki`, `temp_kd`, `heater_pwm`, `spooler_mode`, `motor_setpoint`,
`motor_kp`, `motor_ki`, `motor_kd`, `dc_motor_pwm`, `extrusion_speed`,
`fan_duty`, `target_diameter`, `heating_delay`, `heat_extrude_time`,
`heat_extrude_speed`, `data_delay`, `data_taking_time`, `post_spool_time`,
`sample_rate_hz` (and `start_now` for a direct start).

**Pi → laptop**

| Message | Meaning |
|---|---|
| `{"type": "status", "phase", "remaining", "message", "data_ready", "t0"?}` | sent at every phase change and on every (re)connection; `t0` = recording start on the Pi clock |
| `{"type": "data", "name", "b64", "meta"}` | the recorded table (CSV, base64) + `meta`: `t0`, `t_end`, `rows`, `rate_hz`, `boot` |
| `{"type": "event", "event": "no_data", "message"}` | Retrieve Data before any data exists |
| `{"type": "sync_reply", "id", "t1", "t2", "t3", "boot"}` | ping answer: `t2` = ping arrival, `t3` = reply departure, both on the Pi clock; `boot` = this Pi session's id |

Details:
- Sync pings are answered straight away on the network thread; the few bytes
  every 2 s are negligible for the CPU.
- The recorded table is one large message (>1 MB for a long run); it gets
  its own 30 s send deadline, and a failed send closes the connection so the
  laptop reconnects cleanly instead of reading a truncated message.
- Diameter lines from an older laptop app are ignored.
- If the connection drops, the Pi goes back to waiting; programs can be
  started in any order.

---

## 8. WiFi hotspot

The Pi creates its own WiFi access point, so no university/router network is
needed:

```bash
bash setup_hotspot.sh          # create + start the hotspot
bash setup_hotspot.sh status   # show the hotspot state and the Pi's IP
bash setup_hotspot.sh down     # stop it and return to your normal WiFi
```

| | |
|---|---|
| **SSID** | `FrED_Pi` |
| **Password** | `fredfiber123` |
| **Pi IP** | `192.168.4.1` (the laptop connects here, port `5005`) |

It uses **NetworkManager (`nmcli`)**, the default on Raspberry Pi OS
Bookworm. The values live in `laptop_link.py` (`HOTSPOT_*`, `LINK_PORT`) and
in `setup_hotspot.sh`; keep them in sync if you change them. The screen shows
the Pi's actual address. While it is a hotspot the Pi has **no internet** —
that is intended.

---

## 9. Hardware notes: SPI and the stepper

**SPI** (thermistor ADC MCP3008 and spooler encoder) must be enabled once:

```bash
sudo raspi-config      # Interface Options -> SPI -> Enable, then reboot
```

**Stepper: normal (full-step) mode.** The extrusion stepper runs in the
driver's normal full-step mode: one STEP pulse per motor step, so the pulse
frequency is `RPM × 200 / 60` (`STEPS_PER_REVOLUTION = 200` in `extruder.py`).
The program does **not** drive the driver's microstep mode pins (M0/M1/M2)
at all, because not every FrED wires them to the same GPIO. That keeps the
code the same for every FrED.

An earlier version turned on 1/16 microstepping by driving **BCM GPIO22**
(the DRV8825 **M2** pin on one board) HIGH. That was removed because it only
matched that one board's wiring. A Pi pin keeps its last level until reboot,
so **reboot a Pi once after updating** from a version with microstepping.
Otherwise M2 can stay HIGH and the motor turns 16× too slowly.

Full step is louder and vibrates more than microstepping. If a board needs
microstepping, set it on that board's driver hardware (mode pins or
jumpers). Then the step frequency must be multiplied by the microstep factor
in `Extruder.set_motor_speed` so the RPM setting stays correct.

---

## 10. Other tools in this folder (not used by `main.py`)

A teammate added these in commit `ebab0b1` ("Filtros y nuevo codigo para hacer
experimentos (hace falta probar)"). They are **separate programs**: nothing
in `main.py` imports them, and they only run if launched by hand.

> **Never run any of them at the same time as `main.py`.** They drive the
> same heater, motors and SPI sensors, and `fred_terminal.py` also uses port
> 5005.

### `fred_terminal.py` — headless experiment runner

An alternative to `main.py` with **no graphics** (no PyQt, no matplotlib): it
prints phases, countdowns and readings in the terminal.

- Reads the sensors at 50 Hz, filters them with `signal_filter.py`, and runs
  the heater and spooler PID at 10 Hz (two separate rates).
- Records 14 columns, raw and filtered, which is a different layout from v7.
- Safety cut-offs: heater off above 230 °C, or if the thermistor reads below
  1 °C (disconnected).
- Usage:
  ```bash
  python fred_terminal.py                     # wait for the laptop
  python fred_terminal.py --demo              # short built-in experiment, no laptop
  python fred_terminal.py --sim --demo        # dry run with simulated hardware
  python fred_terminal.py --experiment run.json   # sequence from a JSON file
  # options: --override-camera  --no-color  --csv-dir DIR  --host  --port
  ```
  While running, type `o` to override the camera lock, `a` to abort, `q` to
  quit.
- **Not compatible with the v7 laptop app.** It uses the pre-v7 protocol:
  - It holds every experiment until a diameter stream arrives, which v7 never
    sends, so runs wait until `o` or `--override-camera`.
  - It does not answer clock-sync pings or START RECORDING NOW.
  - It does not send the recording start time, so the laptop cannot merge the
    diameter. The table is saved as FrED sent it, with a note.

### `motor_control.py` — spooler motor bench tool

- Runs a 0.02 s loop: it reads the encoder, computes the RPM, applies an EMA
  filter, and prints time / RPM / input once per second.
- As committed, it computes a PID (`FrED_functions.PID`) but then **overrides
  the motor input with an identification signal**
  (`FrED_functions.least_square(t, 11)`). So it currently runs an
  **open-loop system-identification test**, not speed control.
- Stop it with Ctrl+C to save `FrED_data.txt`.
- Run it with `python motor_control.py`. It imports OpenCV (`cv2`), which
  `setup_install.sh` does not install.

### `heater_control.py` — heater bench tool

```bash
python heater_control.py --mode identify --profile 1   # 1 step, 2 staircase, 3 step up then down
python heater_control.py --mode control --setpoint 90 --kp 1.0 --ki 0.004 --kd 1.8
```

- *identify*: applies a known open-loop PWM profile and logs the temperature,
  for fitting a heater model.
- *control*: runs a closed-loop PID with the gains given on the command line.
- 0.1 s sample time, live matplotlib plot (needs the desktop).
- Safety: heater off above 220 °C or if the thermistor is disconnected.

### `FrED_functions.py` — helpers for the two bench tools

Speed calculation, EMA filters, PID / PI / super-twisting sliding-mode (STSM)
controllers, least-squares identification inputs, motor linearisation,
temperature conversion, an extruder PID, and data saving / plotting. Its
spooler PID uses identified gains **Kp 0.3106, Ki 9.703, Kd 0.02044**. These
fit inside v7's gain limits (1 / 15 / 0.05), so they can be entered in
`main.py`'s motor boxes or the laptop's Experiment tab.

### `signal_filter.py` — EMA filter used by the tools above

An exponential moving average `y = y + α·(x − y)` with presets for 50 Hz
(temperature α = 0.03, spooler RPM α = 0.25, diameter α = 0.20) and helpers to
re-tune them. Since v7, `main.py` does **not** use it: the Pi's graphs show
the control signals directly.

---

## 11. Required libraries

### From `apt` (shared into the venv via `--system-site-packages`)

| apt package | Provides | Why |
|---|---|---|
| `python3-pyqt5` | PyQt5 | the user interface |
| `python3-pyqt5.qtsvg` | PyQt5 QtSvg | needed by matplotlib's Qt5 backend (pip PyQt5 often lacks it) |
| `python3-rpi.gpio` | `RPi.GPIO` | GPIO pins (fan, heater, stepper, spooler) |
| `libatlas-base-dev` | BLAS | numpy / matplotlib |
| `fonts-dejavu` | fonts | graph labels |
| `python3-venv`, `python3-pip`, `python3-dev` | venv, pip, headers | building the environment |

### From `pip` into `fred-venv` (`requirements.txt`)

| pip package | Import | Used for |
|---|---|---|
| `PyYAML` | `yaml` | `calibration.yaml` |
| `numpy` | `numpy` | spooler maths |
| `matplotlib` | `matplotlib` | the two graphs |
| `adafruit-blinka` | `board`, `busio`, `digitalio` | SPI / pins |
| `adafruit-circuitpython-mcp3xxx` | `adafruit_mcp3xxx` | MCP3008 ADC (thermistor) |
| `spidev` | `spidev` | spooler encoder over SPI |

Standard library: `threading`, `time`, `math`, `socket`, `subprocess`,
`json`, `uuid`, `csv`, `collections`, `typing`.

---

## 12. Modules and tuning constants

| File | Role |
|---|---|
| `main.py` | entry point: starts the GUI and the hardware-control thread (polls every 2 ms) |
| `user_interface.py` | PyQt5 interface, the two graphs, the Pi clock `now()`, setpoint/gain accessors |
| `experiment.py` | experiment state machine: phases, START RECORDING NOW, one-tick control + logging, recorded table |
| `laptop_link.py` | TCP server for the laptop's commands and clock-sync pings (replaced `external_diameter.py`) |
| `extruder.py` | heater (thermistor, PID, 1 s mean) + stepper (normal full-step mode) |
| `spooler.py` | spooling motor (encoder, windowed RPM, PID, calibration) |
| `fan.py` | cooling fan |
| `database.py` | logged data + manual CSV export |
| `calibration.yaml` | motor calibration (duty ↔ RPM line) |
| `setup_install.sh`, `setup_hotspot.sh`, `start_fred.sh`, `requirements.txt` | install, hotspot, launch |
| `fake_gpio.py`, `Moving_AVG_TEMP.py` | old test helpers (not used by `main.py`) |
| `fred_terminal.py`, `motor_control.py`, `heater_control.py`, `FrED_functions.py`, `signal_filter.py` | teammate's separate tools (section 10) |

| Constant | Default | Meaning |
|---|---|---|
| Sampling rate box | 50 Hz | control + data rate (manual mode; experiments bring their own) |
| `UserInterface.MOTOR_GAIN_LIMITS` | (1, 15, 0.05) | spooler Kp / Ki / Kd limits |
| `UserInterface.SAMPLE_RATE_LIMITS` | (1, 100) Hz | allowed sampling rates |
| `Spooler.RPM_WINDOW` | 0.1 s | speed measurement window |
| `Thermistor.AVERAGE_WINDOW` | 1.0 s | temperature mean for the PID |
| `main.LOOP_SLEEP` | 0.002 s | control-thread poll period |
| `Plot.REDRAW_INTERVAL_MS`, `Plot.MAX_DRAWN_POINTS` | 100 ms, 1500 | graph refresh and thinning (display only) |
| `LaptopLink.SEND_TIMEOUT`, `READ_TIMEOUT`, `IP_CACHE_S` | 30 s, 0.5 s, 10 s | network timing |

---

## 13. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Installer can't download (`not valid yet`, certificate errors) | wrong clock; the installer fixes it first. If it still fails, the Pi has no internet (the hotspot has none): `bash setup_hotspot.sh down`, join a normal network, re-run |
| `cannot import 'QtSvg' from 'PyQt5'` | `sudo apt install python3-pyqt5 python3-pyqt5.qtsvg`; make sure `fred-venv` was built with `--system-site-packages` |
| GUI does not appear / `qt.qpa.plugin` errors | run from the Pi's desktop session (or with `DISPLAY` set), not a bare SSH session |
| Where is the diameter graph? | on the laptop (v7) |
| Laptop can't connect | check `bash setup_hotspot.sh status`, that the laptop joined `FrED_Pi`, and that `main.py` is running (it opens port 5005); make sure `fred_terminal.py` is not also running |
| Laptop shows `clock sync: waiting` / "FrED is running older software" | the Pi runs pre-v7 code: `git pull`, restart |
| Spooler oscillates at a high sampling rate | lower the rate (10 Hz = the original behaviour) and report it (section 4) |
| Achieved rate stays orange | the requested rate is too high for the loop; lower it |
| Retrieve Data waits forever | update the Pi (the 30 s send deadline fix); the laptop resets the link after 60 s |

---

## Status

v7 was tested on Windows by running this exact program with simulated
hardware against the real laptop app with a simulated camera: phases, START
RECORDING NOW (skip-ahead and direct start), abort, one-tick logging (every
row fresh), clock sync (t = 0 within 0.05 ms) and retrieval all worked. **Not
yet verified on the real machine:** spooler stability at 50 Hz with the
windowed speed, and the sync quality over the real hotspot.
