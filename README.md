# FrED — External Computer Vision (WiFi), v7

Software for the **MIT FrED** (Fiber Extrusion Device), modified so that the
fiber-diameter computer vision runs on an **external laptop** instead of the
Raspberry Pi.

- The **laptop** measures the fiber with its own camera, graphs the diameter
  live, and records every frame.
- The **Raspberry Pi** runs the machine (heater, extrusion stepper, DC
  spooling motor, fan) and executes **fully automated experiments** sent from
  the laptop.
- When an experiment's data is retrieved, both data sets are merged onto
  **one clock-synchronised timeline** in the same CSV + Excel files.

| Folder | Runs on | What it is | Details |
|---|---|---|---|
| [`fred-device-extcv-pi4v6/`](fred-device-extcv-pi4v6/) | **Raspberry Pi 4** | Machine control: PyQt5 interface with temperature + spooler graphs, the control loops, the WiFi hotspot + command server, the experiment engine. Entry point `main.py` (via `start_fred.sh`). | [Pi README](fred-device-extcv-pi4v6/README.md) |
| [`FrEDFiberMeasurewithStreamingv6/`](FrEDFiberMeasurewithStreamingv6/) | **Windows laptop** | Fiber measurement (tkinter + OpenCV): threaded camera pipeline, live diameter graph, calibration, the Experiment tab, START RECORDING NOW / MARK STEADY STATE, the merged export. Entry point `fiber_measure.py` (via `Run FrED Fiber Measure.bat`). | [Laptop README](FrEDFiberMeasurewithStreamingv6/README.md) |

This page is the overview. Each folder's README is the complete manual.

---

## How it fits together

```
          LAPTOP (Windows)                                   RASPBERRY PI 4 (FrED)
 ┌─────────────────────────────────┐                ┌──────────────────────────────────┐
 │ camera ─► capture thread        │                │ hardware thread (polls every 2ms)│
 │            (timestamp)          │    WiFi TCP    │   one tick at the sample rate:   │
 │         ─► measure thread       │   (FrED_Pi,    │   heater PID ─ spooler PID ─     │
 │            (every frame)        │  192.168.4.1   │   stepper ─ fan ─ data row       │
 │         ─► measurement log ─────┼──────┐ :5005)  │                                  │
 │            (laptop clock)       │      │         │ experiment state machine         │
 │ live diameter graph             │  commands ───► │   HEATING ► EXTRUDING ► SETTLE   │
 │ Experiment tab, START NOW,      │  sync pings ─► │   ► RECORDING ► SPOOLING ►       │
 │ MARK STEADY STATE, ABORT        │ ◄─ status (t0) │   COMPLETE                       │
 │                                 │ ◄─ sync reply  │ graphs: temperature, spooler RPM │
 │ Retrieve Data: merge FrED rows  │ ◄─ data table  │ (Pi clock, monotonic)            │
 │ + camera frames ─► CSV / xlsx   │                │                                  │
 └─────────────────────────────────┘                └──────────────────────────────────┘
```

| Responsibility | Laptop | Pi |
|---|---|---|
| Measure the diameter | ✔ every camera frame | — |
| Graph | diameter | temperature, spooler RPM |
| Control heater / stepper / spooler / fan | — | ✔ |
| Run the experiment sequence | sends it | ✔ runs it |
| Record | diameter (laptop clock) | temperature, spooler, setpoints, gains (Pi clock) |
| Line the two up | ✔ clock sync + merge | answers sync pings, reports t0 |

Since v7 the diameter is **not** sent to the Pi. The link carries only
commands (`experiment`, `start_now`, `abort`, `get_data`), a few-byte sync ping
every 2 s, and FrED's replies (`status`, `sync_reply`, `data`). This freed the
Pi's CPU for its control loops.

---

## Quick start

**Raspberry Pi**

```bash
cd fred-device-extcv-pi4v6
bash setup_install.sh    # fresh Pi only
git pull                 # to update an existing Pi (then restart the program)
bash setup_hotspot.sh    # once per boot: start the FrED_Pi WiFi hotspot
bash start_fred.sh       # run FrED
```

**Laptop** (once: `setup_install.bat`)

1. Join the Wi-Fi **`FrED_Pi`** (password `fredfiber123`).
2. Double-click **`Run FrED Fiber Measure.bat`** in
   `FrEDFiberMeasurewithStreamingv6/`.
3. **Calibrate** the camera (px → mm) — *Measure & Connect* tab.
4. *Measure & Connect* → IP `192.168.4.1`, port `5005` → **Connect**.

---

## Running an experiment (operator's checklist)

1. **Prepare** — camera running and calibrated (green box on the fiber);
   laptop connected (status shows `clock sync ±… ms`).
2. **Configure** the *Experiment (FrED)* tab: sequence times, **FrED sample
   rate**, heater and spooler modes and gains (spooler Kp ≤ 1, Ki ≤ 15,
   Kd ≤ 0.05), extrusion speed, fan, target diameter, file name / folder.
3. **Start** — **Send & Start Experiment** (timed sequence), or **START
   RECORDING NOW** with nothing running (starts directly in recording).
4. **Watch** — the live diameter graph on the laptop; temperature and spooler
   on FrED's screen.
   - Press **START RECORDING NOW** the moment the fiber drops. FrED skips the
     rest of the warm-up and records from that instant.
   - Press **MARK STEADY STATE** when the flow looks steady. It only marks the
     data.
   - **ABORT** (laptop) or any red **STOP** (FrED) stops every system.
5. **Retrieve Data** once recording has ended. Keep the laptop app open until
   then: the diameter data lives there.

The sequence FrED runs:

**HEATING → HEATING + EXTRUSION → SETTLE → RECORDING → EXTRA SPOOLING → COMPLETE**

| Phase | Heater | Stepper | Spooler | Fan | Recorded |
|---|---|---|---|---|---|
| HEATING | on | off | off | off | no |
| HEATING + EXTRUSION | on | priming rate | off | off | no |
| SETTLE | on | extrusion speed | on | on | no |
| RECORDING | on | extrusion speed | on | on | **yes** |
| EXTRA SPOOLING | off | off | on | off | no (data ready) |

---

## The data you get

Retrieve Data saves three files:

| File | Contents |
|---|---|
| `<name>.csv` | FrED's rows (one per control tick) with the diameter merged in — 23 columns: temperature data + `Temp new reading`, `Diameter (mm)` (filtered), `Diameter raw (mm)`, `Diameter new frame`, `Diameter camera frame #`, setpoints, fan, extruder, spooler data + `Spooler new reading`, `Steady state` |
| `<name>_camera.csv` | every camera frame at full rate on the same timeline |
| `<name>.xlsx` | both tables, formatted, with native Diameter / Temperature / Spooler charts, plus a **Run info** sheet (alignment method and estimated error, rates, frame counts, steady-state time, every parameter) |

The flag columns tell real measurements from repeated values. `1` means a
new camera frame or a fresh sensor reading in that row; `0` means the value is
repeated only to fill the row. The column-by-column reference is in the
laptop README, section 10.

---

## Time synchronisation (summary)

1. Each machine timestamps its own samples on its own monotonic clock at the
   moment of acquisition.
2. Every 2 s the laptop pings FrED. The four timestamps of each exchange give
   the clock offset with an error of at most half the round trip (the NTP
   method). The fastest exchange of every 10 s is kept, and a straight-line
   fit removes the slow drift between the two clocks.
3. t = 0 is the instant FrED starts recording, reported on FrED's clock and
   mapped onto the laptop clock. Both data sets therefore start at the same
   instant, whatever the WiFi delay.
4. Each FrED row takes the latest camera frame at or before its time, never
   interpolated.

Tested: t = 0 within 0.02–0.05 ms end to end (loopback) and within 0.3 ms in a
simulation with spiky WiFi delays; the naive "arrival time" method would be
off by ~7 ms on average and up to 300 ms. On the real hotspot expect a few ms.
Each file's *Run info* states its own estimate. Full explanation and error
budget: laptop README, section 9.

---

## Performance

| | v6 | v7 |
|---|---|---|
| Camera frames measured per second (this laptop's ~30 fps camera) | 21.4 | **29.8** (all of them, 0 dropped) |
| Detector time per 640 × 480 frame | 3.4 ms | **1.6 ms** (identical results) |
| Diameter messages the Pi processes | ~20 per second | **0** |
| FrED control rate / data rate | 10 Hz / 50 Hz (repeats) | **one rate** (50 Hz setting ≈ 46–48 Hz achieved), fresh readings every row |
| Spooler speed jitter at 50 Hz (simulation) | 0.34 RPM | **0.15 RPM** (0.1 s window; identical to the original at 10 Hz) |

---

## Other tools in the repository

A teammate added separate tools to the Pi folder (commit `ebab0b1`):
`fred_terminal.py` (a headless experiment runner), `motor_control.py` and
`heater_control.py` (bench tools for identification and PID tests),
`FrED_functions.py` and `signal_filter.py` (their helpers). **The v7 program
does not use them**; they run only if launched by hand, and never together
with `main.py`. `fred_terminal.py` uses the pre-v7 protocol and is **not
compatible with the v7 laptop app**. See the Pi README, section 10.

---

## Version history

| Version | Change |
|---|---|
| v1–v2 | CV moved off the Pi; diameter streamed over a **USB serial** link. |
| v3 | Link switched to **WiFi** (Pi hotspot + TCP). Hardware-validated. |
| v4 | Laptop: sample-count auto-stop. Pi: per-subsystem **STOP** buttons + **Passive Monitoring**. |
| v5 | Pi: **Reset Graphs**, adjustable sampling rate with live read-out, plot redraw moved off the control thread. |
| v6 | **Remote automated experiments** (bidirectional protocol), es-MX CSV export, controls locked during runs, diameter jitter filter, formatted Excel with charts, Retrieve Data progress dialog and hang fix. |
| **v7** | **Diameter native to the laptop** (no streaming; threaded capture measures every frame; live graph on the laptop), **clock-synchronised merge** with new-sample flags and a full-rate camera sheet, **START RECORDING NOW**, **MARK STEADY STATE**, FrED control + data at **one rate** with windowed speed / temperature, spooler gain limits **1 / 15 / 0.05**, monotonic Pi clock. |

**v7 needs both sides updated** (`git pull` on the Pi).

### First run on the real machine — what to check

1. The spooler is stable at the chosen sample rate (try 50 Hz; 10 Hz gives
   the original behaviour).
2. The laptop's `clock sync ±… ms` over the real hotspot (expect a few ms).
3. *Run info* of the first file: the achieved FrED rate, camera fps, and rows
   with a diameter.

---

## Repository layout

```
FrEDExtCV/
├── README.md                          # this overview
├── fred-device-extcv-pi4v6/           # Raspberry Pi — machine control (Pi README)
│   ├── main.py                        #   entry point (hardware thread + GUI)
│   ├── user_interface.py              #   PyQt5 GUI: temperature + spooler graphs, Pi clock
│   ├── experiment.py                  #   experiment state machine, one-tick control + logging
│   ├── laptop_link.py                 #   WiFi TCP server: commands, clock-sync replies
│   ├── extruder.py / spooler.py / fan.py / database.py
│   ├── setup_install.sh / setup_hotspot.sh / start_fred.sh / requirements.txt
│   ├── calibration.yaml
│   └── fred_terminal.py, motor_control.py, heater_control.py,
│       FrED_functions.py, signal_filter.py   # teammate's separate tools (not used by main.py)
└── FrEDFiberMeasurewithStreamingv6/   # Windows laptop — CV, graph, experiments (Laptop README)
    ├── fiber_measure.py               #   the application
    ├── Run FrED Fiber Measure.bat     #   double-click launcher
    ├── setup_install.py / setup_install.bat / requirements.txt
    └── calibration.json
```

Working rule for this repo: every change is committed **and pushed** so the
local copy and GitHub always match. The Pi updates with `git pull`.
