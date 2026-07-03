# FrED — External Computer Vision (WiFi)

Software for the **MIT FrED** (Fiber Extrusion Device), modified so that the
fiber-diameter computer vision runs on an **external computer** instead of the
Raspberry Pi. The laptop measures the fiber with its own camera and streams the
diameter to the Pi **over WiFi**; the Pi runs all the machine control (heater,
extrusion stepper, DC spooling motor, fan) and can execute **fully automated
experiments** sent from the laptop.

The system is two programs, one per machine:

| Folder | Runs on | What it is |
|---|---|---|
| [`fred-device-extcv-pi4v6/`](fred-device-extcv-pi4v6/) | **Raspberry Pi 4** | FrED device control: PyQt5 interface, heater/stepper/spooler/fan loops, WiFi hotspot + TCP server that receives the diameter, and the automated-experiment engine. Entry point: `main.py`. |
| [`FrEDFiberMeasurewithStreamingv6/`](FrEDFiberMeasurewithStreamingv6/) | **Windows laptop** | Fiber measurement app (tkinter + OpenCV): live camera detection, px→mm calibration, CSV/xlsx logging, streaming to the Pi, and the **Experiment (FrED)** tab for remote experiments. Entry point: `fiber_measure.py`. |

Each folder has its own detailed README with installation, usage and
troubleshooting; this page is the overview of how they work together.

## How the two sides connect

The **Pi is its own WiFi hotspot** — no lab/university network is needed:

| | |
|---|---|
| WiFi name (SSID) | `FrED_Pi` |
| Password | `fredfiber123` |
| Pi address | `192.168.4.1`, TCP port `5005` |

The Pi listens as a TCP **server**; the laptop connects as the **client** and
the two exchange newline-delimited JSON. The laptop streams one message per
measurement:

```json
{"v": 1, "d": 0.352, "u": "mm", "t": 12.345, "found": true}
```

The same socket is **bidirectional** (v6): the laptop can also send
`{"type": "experiment", ...}` / `{"type": "get_data"}` / `{"type": "abort"}`,
and the Pi answers with `{"type": "status", ...}` (phase + time remaining) and
`{"type": "data", ...}` (the recorded CSV, base64-encoded). Either program can
be started first; the Pi automatically returns to *waiting for laptop* if the
connection drops.

## Quick start

**Raspberry Pi** (once: `bash setup_install.sh`, then per boot):

```bash
cd fred-device-extcv-pi4v6
bash setup_hotspot.sh    # start the FrED_Pi WiFi hotspot
bash start_fred.sh       # run the FrED interface (activates fred-venv)
```

**Laptop** (once: `setup_install.bat` or `python setup_install.py`):

1. Join the **`FrED_Pi`** WiFi (password `fredfiber123`).
2. Double-click **`Run FrED Fiber Measure.bat`** in
   `FrEDFiberMeasurewithStreamingv6/` (or run `python fiber_measure.py`).
3. **Calibrate** the camera (px→mm) so streamed values are in millimetres.
4. In *Stream to FrED Pi (WiFi)*: IP `192.168.4.1`, port `5005` → **Connect**
   (streaming auto-starts).
5. On the Pi, press **Start Diameter/Camera Loop** to graph the diameter — or
   use the laptop's **Experiment (FrED)** tab to run a whole experiment
   remotely and retrieve the data as CSV + xlsx.

## Automated experiments (v6)

From the laptop's **Experiment (FrED)** tab you define the sequence timing
(heating time, heating+extrusion time and its own extrusion rate, settle time,
data-taking time, extra post-run spooling time), heater and spooler modes
(closed-loop setpoint+PID or open-loop PWM), stepper speed, fan duty and target
diameter. FrED then runs the sequence on its own:

**HEATING → HEATING+EXTRUSION → SETTLE → RECORDING → EXTRA SPOOLING → COMPLETE**

During heating+extrusion only the heater and the extrusion stepper run, at an
independently configurable extrusion rate. After recording ends everything
stops **except the spooler**, which keeps coiling the extruded fiber for the
user-set extra spooling time.

The streamed diameter is **jitter-filtered on the Pi** as it arrives (median +
short average: single-frame spikes removed, ~0.2–0.4 s lag); the export keeps
both the filtered `Diameter (mm)` and the as-received `Diameter raw (mm)`.

All data is timestamped on the Pi's clock (t = 0 at the start of recording) and
returned on **Retrieve Data** as a semicolon-delimited CSV plus an **xlsx copy
with bold color-coded headers and native Excel charts** (Diameter, Temperature,
Spooler RPM vs time — the Pi's three graphs) placed next to the data.
FrED's graphs are cleared the instant recording starts, so they show exactly
the exported window. While an experiment runs, **every** control on FrED's
screen (start buttons, PID gains, setpoints, sliders) is disabled and shown
grayed-out — except the red **STOP** buttons. Aborting (a red STOP on FrED or
the laptop's **Abort** button) stops **all** systems — heater, stepper,
spooler and fan.

## Version history

| Version | Change |
|---|---|
| v1–v2 | CV moved off the Pi; diameter streamed over a **USB serial** link. |
| v3 | Link switched to **WiFi** (Pi hotspot + TCP). Hardware-validated. |
| v4 | Laptop: sample-count auto-stop. Pi: per-subsystem **STOP** buttons + **Passive Monitoring** mode. |
| v5 | Pi: **Reset Graphs** button, adjustable sampling rate with live read-out, plot redraw decoupled from the control thread (fixes CSV starving at ~4 rows/s). |
| **v6 (this repo)** | **Bidirectional protocol + remote automated experiments**, es-MX friendly CSV export (`;` delimiter, `,` decimals), controls locked during runs, streaming auto-start on connect. |

> Known limitation (v6): control loops run at the original 10 Hz while
> experiment data is logged at the selected rate (default 50 Hz). Making the DC
> motor stable with control **and** logging at the same high rate is planned
> future work.

## Repository layout

```
FrEDExtCV/
├── fred-device-extcv-pi4v6/          # Raspberry Pi — device control (see its README)
│   ├── main.py                       #   entry point
│   ├── user_interface.py             #   PyQt5 GUI
│   ├── external_diameter.py          #   WiFi TCP server (diameter in, commands in/out)
│   ├── experiment.py                 #   automated-experiment state machine
│   ├── extruder.py / spooler.py / fan.py / database.py
│   ├── setup_install.sh / setup_hotspot.sh / start_fred.sh
│   └── calibration.yaml / requirements.txt
└── FrEDFiberMeasurewithStreamingv6/  # Windows laptop — CV + streaming (see its README)
    ├── fiber_measure.py              #   entry point (app)
    ├── Run FrED Fiber Measure.bat    #   double-click launcher (no console needed)
    ├── setup_install.py / setup_install.bat
    └── calibration.json / requirements.txt
```
