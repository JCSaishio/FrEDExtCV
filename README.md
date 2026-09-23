# FrED — External Computer Vision (WiFi)

Software for the **MIT FrED** (Fiber Extrusion Device), modified so that the
fiber-diameter computer vision runs on an **external computer** instead of the
Raspberry Pi. The laptop measures, graphs and records the diameter with its own
camera; the Pi runs all the machine control (heater, extrusion stepper, DC
spooling motor, fan) and executes **fully automated experiments** sent from the
laptop. When the data is retrieved, both sides are merged onto one
clock-synchronised timeline.

The system is two programs, one per machine:

| Folder | Runs on | What it is |
|---|---|---|
| [`fred-device-extcv-pi4v6/`](fred-device-extcv-pi4v6/) | **Raspberry Pi 4** | FrED device control: PyQt5 interface (temperature + spooler graphs), heater/stepper/spooler/fan loops, WiFi hotspot + TCP command server, and the automated-experiment engine. Entry point: `main.py`. |
| [`FrEDFiberMeasurewithStreamingv6/`](FrEDFiberMeasurewithStreamingv6/) | **Windows laptop** | Fiber measurement app (tkinter + OpenCV): every camera frame measured on background threads, live diameter graph, px→mm calibration, the **Experiment (FrED)** tab, START RECORDING NOW / MARK STEADY STATE, and the merged CSV + Excel export. Entry point: `fiber_measure.py`. |

Each folder has its own detailed README; this page is the overview.

## How the two sides connect

The **Pi is its own WiFi hotspot** — no lab/university network is needed:

| | |
|---|---|
| WiFi name (SSID) | `FrED_Pi` |
| Password | `fredfiber123` |
| Pi address | `192.168.4.1`, TCP port `5005` |

The Pi listens as a TCP **server**; the laptop connects as the **client**. Since
v7 the **diameter is not streamed** — it stays on the laptop, which frees the
Pi's CPU for its PID loops. The link only carries newline-delimited JSON
commands:

- laptop → Pi: `experiment`, `start_now`, `abort`, `get_data`, and a `sync`
  ping every 2 s;
- Pi → laptop: `status` (phase, time left, recording start `t0`), `data` (the
  recorded table + timing), `sync_reply`.

Either program can be started first; the Pi returns to *waiting for laptop* if
the connection drops and re-announces its phase on reconnect.

## Quick start

**Raspberry Pi** (once on a fresh Pi: `bash setup_install.sh`; to update:
`git pull`), then per boot:

```bash
cd fred-device-extcv-pi4v6
bash setup_hotspot.sh    # start the FrED_Pi WiFi hotspot
bash start_fred.sh       # run the FrED interface (activates fred-venv)
```

**Laptop** (once: `setup_install.bat` or `python setup_install.py`):

1. Join the **`FrED_Pi`** WiFi (password `fredfiber123`).
2. Double-click **`Run FrED Fiber Measure.bat`** in
   `FrEDFiberMeasurewithStreamingv6/` (or run `python fiber_measure.py`).
3. **Calibrate** the camera (px→mm).
4. *Measure & Connect* tab: IP `192.168.4.1`, port `5005` → **Connect**.
5. *Experiment (FrED)* tab: set the run → **Send & Start Experiment**; press
   **START RECORDING NOW** when the fiber drops and **MARK STEADY STATE** when
   the flow looks steady; afterwards **Retrieve Data**.

## Automated experiments

From the laptop you define the sequence timing (heating, heating+extrusion with
its own extrusion rate, settle, data-taking, extra post-run spooling), the
**FrED sample rate**, heater and spooler modes (closed-loop setpoint+PID or
open-loop PWM; spooler gains limited to Kp ≤ 1, Ki ≤ 15, Kd ≤ 0.05), stepper
speed, fan duty and target diameter. FrED then runs:

**HEATING → HEATING+EXTRUSION → SETTLE → RECORDING → EXTRA SPOOLING → COMPLETE**

- **START RECORDING NOW** skips the rest of the warm-up the moment the fiber
  drops (or, with no run active, starts a run directly in recording).
- **MARK STEADY STATE** only marks the data: a `Steady state` column (0/1).
- On FrED, the temperature PID, spooler PID and the logged row run on **one
  tick at one rate**; speed and temperature are measured over fixed 0.1 s / 1 s
  windows so they stay clean at 50 Hz (10 Hz = exactly the original maths).
- While a run is active every control on FrED's screen is locked except the red
  STOP buttons; an abort (FrED STOP or laptop ABORT) stops **all** systems.

**Retrieve Data** saves `<name>.csv` (FrED's rows with the diameter merged in),
`<name>_camera.csv` (every camera frame) and `<name>.xlsx` (formatted table,
native charts, full-rate camera sheet, *Run info* sheet).

### Time synchronisation

Each machine timestamps its own samples on its own monotonic clock. NTP-style
ping/replies every 2 s measure the clock offset (fastest exchange per 10 s,
straight-line fit for drift), and FrED's recording start is converted onto the
laptop clock, so both data sets share t = 0 regardless of WiFi delay (tested:
0.05 ms on loopback, < 0.3 ms in a simulation with spiky WiFi delays; the
estimated error of each run is in its *Run info* sheet). Each FrED row holds the
latest camera frame at or before its time — `Diameter new frame` (1/0) and
`Diameter camera frame #` show which values are new measurements and which are
repeats; `Temp new reading` / `Spooler new reading` do the same for FrED's
sensors. Details: laptop README, *Time synchronisation*.

## Version history

| Version | Change |
|---|---|
| v1–v2 | CV moved off the Pi; diameter streamed over a **USB serial** link. |
| v3 | Link switched to **WiFi** (Pi hotspot + TCP). Hardware-validated. |
| v4 | Laptop: sample-count auto-stop. Pi: per-subsystem **STOP** buttons + **Passive Monitoring** mode. |
| v5 | Pi: **Reset Graphs** button, adjustable sampling rate with live read-out, plot redraw decoupled from the control thread. |
| v6 | **Bidirectional protocol + remote automated experiments**, es-MX CSV export, controls locked during runs, jitter filter, formatted Excel with charts. |
| **v7** | **Diameter native to the laptop** (no streaming; threaded capture measures every frame; live graph on the laptop), **clock-synchronised merge** with new-sample flags and full-rate camera sheet, **START RECORDING NOW**, **MARK STEADY STATE**, control + data at **one rate** with windowed speed/temperature, spooler gain limits 1 / 15 / 0.05. |

> v7 needs **both** sides updated (`git pull` on the Pi). Not yet verified on
> the real machine: spooler stability at 50 Hz with the windowed speed, and the
> sync quality over the real hotspot (shown live on the laptop and in every
> file's *Run info*).

## Repository layout

```
FrEDExtCV/
├── fred-device-extcv-pi4v6/          # Raspberry Pi — device control (see its README)
│   ├── main.py                       #   entry point
│   ├── user_interface.py             #   PyQt5 GUI (temperature + spooler graphs)
│   ├── laptop_link.py                #   WiFi TCP server (commands, clock sync)
│   ├── experiment.py                 #   automated-experiment state machine
│   ├── extruder.py / spooler.py / fan.py / database.py
│   ├── setup_install.sh / setup_hotspot.sh / start_fred.sh
│   └── calibration.yaml / requirements.txt
└── FrEDFiberMeasurewithStreamingv6/  # Windows laptop — CV, graph, experiments (see its README)
    ├── fiber_measure.py              #   entry point (app)
    ├── Run FrED Fiber Measure.bat    #   double-click launcher (no console needed)
    ├── setup_install.py / setup_install.bat
    └── calibration.json / requirements.txt
```
