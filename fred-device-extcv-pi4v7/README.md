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
| **Extrusion Motor** | extrusion speed (RPM), **Start Stepper**, **Check Steps** (skipped-step check, section 9), **STOP Stepper** |
| **Cooling Fan** | duty slider, STOP / Start Fan |
| **Data Export** | file name + Download CSV File (manual CSV of everything logged) |

While an experiment runs, **every control is disabled** and shown in light
grey, **except the red STOP buttons**, which abort the run.

---

## 4. Control loops and sampling rate

### One rate for control and data

The **Sampling rate (Hz)** box (1–100, default **50**) sets how often the
temperature loop, the spooler loop and the stepper loop run — in manual mode,
in monitoring, and in experiments. An experiment sent from the laptop brings
its own rate (*FrED sample rate* on the laptop), which overrides the box
during the run.

**In manual mode the three loops are independent.** Each runs on its own
timing, only while it is started:

| Loop | Runs while | Started / stopped with |
|---|---|---|
| Temperature | a heater loop is on | Start Temperature Close Loop / Start Heater Open Loop — STOP Heater |
| Spooler | a motor loop is on | Start Motor Close Loop / Start DC Motor Open Loop — STOP Spooling Motor |
| Stepper | the stepper is started | **Start Stepper** — STOP Stepper |

Until this version the stepper was only updated **inside** the two heater
branches of `main.py` (MIT's upstream `main.py` still works that way), so the
Extrusion Motor Speed did nothing unless a heater loop was running. Now the
stepper turns with or without heating — with a cold barrel the screw pushes
against solid plastic, which is why Start Stepper says so when no heater loop
is on.

During an experiment, **one tick** drives everything: the heater PID, the
spooler PID, the stepper, the fan and the recorded data row, in that order and
with the same timestamp. So control and data run at the **same frequency** and
every recorded row holds fresh sensor readings. The stepper loop gates on the
tick period exactly like the temperature and spooler loops, so it stays in
step with the tick.

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

The stepper follows the same rule as every other signal (laptop README,
*Time synchronisation*): each stepper sample is logged with its own time on
this clock (`Database.extruder_timestamps`, also when the stepper is stopped
or a Check Steps runs). In an experiment row, `Extruder RPM` comes from the
same tick as the row. In the manual CSV (*Download CSV File*) the stepper no
longer lines up one-to-one with the motor table's spooler rows, so each row
gets the stepper setting **in force at that row's time** — held, never
interpolated, the same way the laptop puts camera frames on FrED's rows.
(Before, the column was paired by position, which put wrong values on rows
whenever the two loops logged at different times.)

---

## 5. Manual operation: STOP buttons, monitoring, gain limits, CSV

### STOP buttons

Each actuator has its own red STOP button; stopping drives that output to
**0** immediately (not left at its last value):

| Button | Effect |
|---|---|
| **STOP Heater** | turns heating off (open- and closed-loop) and clears the PID state |
| **STOP Spooling Motor** | stops the spooler (open- and closed-loop) and clears its PID state |
| **STOP Stepper** | stops the stepper and aborts a running Check Steps; the speed setting is kept |
| **STOP / Start Fan** | holds the fan at 0 % (toggles back on without losing the slider value) |

Restart heater / motor with their Start buttons and the stepper with **Start
Stepper**. During an experiment any red STOP aborts the whole run (section 6).

### Extrusion stepper

- **Start Stepper** runs the stepper at **Extrusion Motor Speed (RPM)**; the
  speed can be changed while it runs (0 holds it still). It is independent of
  the heater (section 4).
- **STOP Stepper** stops it; press Start Stepper to run it again.
- **Check Steps** turns exactly one revolution at the current speed so you can
  see whether the motor skips steps (section 9). The normal stepper pauses
  during the check and resumes afterwards if it was started.

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
- The manual control loops (heater close/open loop, motor close/open loop,
  the started stepper) are switched off when a run starts, so none of them
  resumes after the run. A running Check Steps is aborted.
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

### Stepper: 1/16 microstepping

The extrusion stepper (DRV8825 driver) runs at **1/16 microstepping**: 16 STEP
pulses per full motor step, so the pulse frequency is
`RPM × 200 × 16 / 60` (1067 pulses/s at the 20 RPM maximum). Microstepping
is much quieter and smoother than full step, which shook the fiber. Every
stepper setting lives in **`stepper_config.py`**, shared by `main.py` and the
skipped-step checker:

| Signal | BCM GPIO | Header pin | Driven to |
|---|---|---|---|
| STEP | 20 | 38 | step PWM, 50 % duty while running |
| DIR | 16 | 36 | HIGH, fixed (extrusion direction) |
| M0 / M1 / M2 | 17 / 27 / 22 | 11 / 13 / 15 | set from `MICROSTEPS` at startup: 1/16 = LOW / LOW / HIGH |

**Changing the resolution:** edit `MICROSTEPS` in `stepper_config.py`, the
section marked *MICROSTEPPING - CHANGE THE RESOLUTION HERE*. The program then
sets M0/M1/M2 and the step frequency to match, so the RPM stays correct:

| `MICROSTEPS` | M0 | M1 | M2 | Mode |
|---|---|---|---|---|
| 1 | LOW | LOW | LOW | full step |
| 2 | HIGH | LOW | LOW | 1/2 |
| 4 | LOW | HIGH | LOW | 1/4 |
| 8 | HIGH | HIGH | LOW | 1/8 |
| **16** | LOW | LOW | **HIGH** | **1/16 (in use)** |
| 32 | HIGH | LOW | HIGH | 1/32 |

- 1/16 only needs **M2** HIGH, so it works on boards where all three mode pins
  are wired (MIT's pins, the same as
  [mit-fredfactory/fred-device](https://github.com/mit-fredfactory/fred-device)
  `main`) and on boards where only M2 reaches the driver. On the board where
  microstepping was first set up, a continuity test found only M2 wired to
  GPIO22; the DRV8825's internal pull-downs then hold M0/M1 LOW.
- 1/2, 1/4, 1/8 and 1/32 also need **M0 and/or M1**: check with a multimeter
  that GPIO17 / GPIO27 reach the driver's M0 / M1 pins first, otherwise the
  motor turns at the wrong speed.
- The pulses come from a software PWM, so keep the pulse rate moderate (1/32
  at 20 RPM is 2133 pulses/s).
- After a change, run a skipped-step check (below).
- All three mode pins are driven at every start, so the resolution is known
  whatever an earlier program left them at (a Pi pin keeps its level until
  reboot or until a program sets it).

The step PWM is reprogrammed only when the RPM setpoint changes, as in MIT's
`StepperMotor.set_speed`: resetting it on every loop pass could swallow step
pulses.

History: v7 briefly ran the stepper in full step (commits `8cd6bd0`,
`6c1d282`) to be independent of each board's mode-pin wiring; microstepping
is back, with every mode selectable in one place.

**Older boards:** in MIT's history, STEP moved from **BCM12** (header pin 32)
to **BCM20** with **PCB 2.2** (June 2025). On a board older than PCB 2.2,
change `STEP_PIN` in `stepper_config.py` to 12.

**`fred_terminal.py`** (section 10) is unchanged: it assumes full step and
does not set the mode pins. After `main.py` has run, M2 stays HIGH (1/16)
until the Pi reboots, so `fred_terminal.py`'s stepper then turns **16×
slower** than asked. Reboot the Pi before using it.

### Skipped-step check

FrED has **no sensor on the extrusion motor**, so no program can detect a
skipped step on its own. The check makes skips visible instead:

1. Put a mark on the motor shaft or coupling (a tape flag works well) and
   note what it points at.
2. The check sends an **exact** number of step pulses: whole revolutions
   (3200 steps per revolution at 1/16).
3. At the end the mark must point exactly where it started. If it is short,
   the motor skipped steps. A stall loses 4 full steps (7.2°) at a time, so
   one slip is easy to see.

The normal program's software PWM cannot count its pulses, so the check sends
them one by one on an absolute schedule (`step_check.py`). A pulse that goes
out late is never followed by a catch-up burst, which could itself cause a
skip; the motor only pauses for a moment. The number of late pulses is shown
as *Pi timing*.

**In `main.py`: Check Steps** (Extrusion Motor panel). One revolution at the
current Extrusion Motor Speed. It is the test to use **under real load**, with
the barrel hot. It asks you to confirm, runs (the normal stepper pauses; STOP
Stepper aborts), then asks whether the mark came back and gives advice if
not. The result is also printed in the terminal.

**In the terminal: `check_stepper.sh`** runs a speed sweep and reports the
fastest speed with no skips. Close `main.py` first. The tool refuses to run
while another FrED program drives the pins, and **closing `main.py`'s window
does not end the program** (its hardware thread keeps running): stop it with
`pkill -f main.py` or Ctrl+C in its terminal. With `main.py` closed the barrel
is cold, so test with the motor decoupled or the barrel empty.

```bash
bash check_stepper.sh                          # 1, 2, 5, 10, 15, 20 RPM, 1 revolution each
bash check_stepper.sh --speeds 1.5 3 6         # your own speeds
bash check_stepper.sh --revs 2                 # 2 revolutions per test
bash check_stepper.sh --microsteps 8           # try another resolution (needs M0/M1 wired)
bash check_stepper.sh --reverse                # turn the other way
python step_check.py --sim                     # dry run off the Pi
```

For each speed: Enter runs it, `s` skips it, `q` finishes. Then answer
whether the mark is back (`y` / `n` / `r` to repeat), and if not, roughly how
many degrees it is off. The tool converts that to full steps and asks whether
to continue to faster speeds. Ctrl+C aborts a running test. The tool forces
the heater output OFF at start: a killed FrED program leaves its pins at
their last level, so the heater could be latched ON.

If steps are skipped, the usual causes are: the speed is too high for the
load, the driver current is too low (raise Vref a little), cold or stiff
plastic in the barrel, or the screw binding mechanically.

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
- **Stepper in full step.** It computes the step frequency for full step and
  does not set the microstep pins, while `main.py` runs 1/16 (section 9). After
  `main.py` has run, M2 stays HIGH until reboot and `fred_terminal.py`'s
  stepper turns 16× slower than asked: reboot the Pi before using it.

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
| `extruder.py` | heater (thermistor, PID, 1 s mean) + stepper (own loop, 1/16 microstepping, Check Steps) |
| `stepper_config.py` | stepper pins and **microstep resolution** (`MICROSTEPS`), shared by `extruder.py` and `step_check.py` |
| `step_check.py`, `check_stepper.sh` | skipped-step check: exact step counts (Check Steps button + terminal speed sweep, section 9) |
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
| `stepper_config.MICROSTEPS` | 16 | stepper microstep resolution (1, 2, 4, 8, 16, 32) |
| `UserInterface.STEP_CHECK_REVOLUTIONS` | 1 | revolutions turned by Check Steps |
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
| Stepper does not turn when the speed is set | press **Start Stepper** (the stepper has its own start since the loops became independent) |
| Stepper turns at the wrong speed (e.g. 16× too slow or 2× too fast) | `MICROSTEPS` does not match the wiring: 1/16 only needs M2; other modes need M0/M1 wired (section 9). After `main.py`, `fred_terminal.py` runs 16× slow until a reboot |
| Stepper knocks / the shaft mark comes back short | skipped steps: run Check Steps or `bash check_stepper.sh`, then lower the speed, raise Vref a little, heat the barrel, or check the screw for binding |
| `check_stepper.sh` says another FrED program is running | closing `main.py`'s window leaves it running: `pkill -f main.py`, then retry |

---

## Status

v7 was tested on Windows by running this exact program with simulated
hardware against the real laptop app with a simulated camera: phases, START
RECORDING NOW (skip-ahead and direct start), abort, one-tick logging (every
row fresh), clock sync (t = 0 within 0.05 ms) and retrieval all worked. **Not
yet verified on the real machine:** spooler stability at 50 Hz with the
windowed speed, and the sync quality over the real hotspot.

The independent stepper loop, 1/16 microstepping and the skipped-step check
were tested the same way (real `main.py` + interface, simulated GPIO):
Start/STOP Stepper with and without heater loops, 1/16 frequencies and mode
pins, experiments taking over and the manual stepper not resuming, exactly
3200 pulses per Check Steps revolution with no overlap with the PWM, aborts,
and the held Extruder RPM in the manual CSV. **Not yet verified on the real
machine.**
