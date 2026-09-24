# FrED Fiber Measure — v7 (laptop side)

Real-time **fiber diameter measurement** from a USB camera for the MIT FrED
(Fiber Extrusion Device), plus **remote control of automated FrED
experiments** over WiFi, and a clock-synchronised export that merges the
laptop's diameter data with FrED's temperature / spooler data.

This program runs on the **Windows laptop**. Its partner is the Raspberry Pi
program in [`../fred-device-extcv-pi4v6/`](../fred-device-extcv-pi4v6/), which
runs the heater, extruder, spooler and fan. Both sides must run v7.

---

## Contents

1. [What v7 changed and why](#1-what-v7-changed-and-why)
2. [Install and run](#2-install-and-run)
3. [Tour of the window](#3-tour-of-the-window)
4. [Camera, speed and the measurement pipeline](#4-camera-speed-and-the-measurement-pipeline)
5. [Calibration and detection parameters](#5-calibration-and-detection-parameters)
6. [Connecting to FrED](#6-connecting-to-fred)
7. [Running an experiment, step by step](#7-running-an-experiment-step-by-step)
8. [Experiment tab — every field](#8-experiment-tab--every-field)
9. [Time synchronisation — how the two data sets are lined up](#9-time-synchronisation--how-the-two-data-sets-are-lined-up)
10. [Exported files — every column](#10-exported-files--every-column)
11. [Recording on the laptop only (without FrED)](#11-recording-on-the-laptop-only-without-fred)
12. [How the diameter is measured](#12-how-the-diameter-is-measured)
13. [Code map and tuning constants](#13-code-map-and-tuning-constants)
14. [Test results](#14-test-results)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. What v7 changed and why

**Problem.** Up to v6 the laptop streamed ~20 diameter messages per second to
the Raspberry Pi, which parsed, filtered, stored and graphed them. On the Pi
that work competed with the heater and spooler PID loops for the CPU.

**v7 keeps the diameter entirely on the laptop:**

| | v6 | v7 |
|---|---|---|
| Where the diameter is measured | laptop | laptop |
| Where it is graphed | Pi | **laptop** (live graph under the video) |
| Where it is recorded | Pi (from the stream) | **laptop**, every camera frame, on the laptop clock |
| What travels over WiFi | ~20 diameter messages/s + commands | **commands only** + one tiny clock-sync ping every 2 s |
| How diameter and FrED data meet | Pi logged the "latest received" value | **clock-synchronised merge** on Retrieve Data |
| Frames measured (this laptop's camera) | 21.4 per second | **29.8 per second** — every frame the camera delivers |

New in v7:

- **Threaded camera pipeline** — every frame is captured and timestamped on
  one thread and measured on another; the window only displays results.
- **Faster detector** — only the fiber's own strip is rotated, not the whole
  frame (about 2× faster, identical diameters).
- **Live diameter graph** under the video.
- **START RECORDING NOW** — press it the moment the fiber drops.
- **MARK STEADY STATE** — flags the data from the moment the flow looks
  steady (it changes nothing else).
- **FrED sample rate** chosen per experiment (FrED's control *and* data rate).
- **Spooler PID gain limits** Kp ≤ 1, Ki ≤ 15, Kd ≤ 0.05.
- **Export flags** that tell real measurements from repeated values:
  `Diameter new frame`, `Temp new reading`, `Spooler new reading`, plus the
  camera frame number and a full-rate camera sheet.

---

## 2. Install and run

Python runs through Anaconda (`C:/Users/saish/anaconda3/python.exe`).

**Install once:**

```bash
"C:/Users/saish/anaconda3/python.exe" -m pip install -r requirements.txt
```

or double-click `setup_install.bat` (or run `setup_install.py`). Needed
packages: `opencv-python`, `numpy`, `Pillow`, `openpyxl`. The WiFi link uses
Python's built-in `socket` and the graph is plain Tk, so nothing else is
required.

**Run:** double-click **`Run FrED Fiber Measure.bat`** (no console window; the
.bat can be copied anywhere, e.g. the Desktop — if you move the app folder,
edit the `APP_DIR` line inside it). Or from a terminal:

```bash
"C:/Users/saish/anaconda3/python.exe" fiber_measure.py
```

---

## 3. Tour of the window

```
┌──────────────────────────────────────────┬─────────────────────┐
│ [Live feed] [Mask (processed)]   fps line│ Measure & Connect   │
│                                          │ Experiment (FrED)   │
│        camera feed (one at a time)       │   (tabs)            │
│                                          │                     │
├──────────────────────────────────────────┤                     │
│ Diameter  0.3512 mm (filtered)  Show last│                     │
│        diameter vs time graph            │                     │
├──────────────────────────────────────────┤                     │
│ [START RECORDING NOW] [MARK STEADY STATE]            [ABORT]   │
│ FrED: phase (time left) | steady state marked | sync ±x ms     │
└──────────────────────────────────────────┴─────────────────────┘
│ status bar                                                     │
```

**Camera feed (top).** Two toggle buttons choose what is shown:
- **Live feed** — the camera image with the detected fiber box (green) and
  the diameter tick (red) drawn on it.
- **Mask (processed)** — the black/white image after blur, threshold and
  clean-up, i.e. exactly what the detector measures. A blob rejected by the
  *Min area* setting is outlined in red.

Only the visible feed is drawn, which saves CPU. The **fps line** at the top
right reads, e.g., `Camera 640x480 YUY2: 29.8 fps | measured 29.8 fps |
dropped 0`: the camera rate, the measurement rate (they should match) and how
many frames were ever dropped because measuring fell behind (should stay 0).

**Diameter graph (middle, always visible).**
- light blue = every measured frame (raw)
- dark blue = filtered (median of 5 + mean of 3 detected frames)
- dashed red = the target diameter from the Experiment tab (shown when in mm
  and near the data)
- green vertical line = FrED recording start (t = 0)
- orange vertical line = your steady-state mark
- **Show last**: 10 s, 30 s, 1 min, 2 min, 5 min, or *Whole run*
- The x-axis is *Time (s)* since the app started, and switches to *Time since
  FrED recording start (s)* as soon as FrED starts recording — the same time
  axis as the exported files.

**Run bar (bottom).** The buttons you need while watching the fiber:
START RECORDING NOW, MARK STEADY STATE and ABORT (see section 7), plus a
status line: FrED's phase and a live countdown, the steady-state mark time,
and the clock-sync quality.

**Right-hand tabs.**
- *Measure & Connect* — camera, live reading, calibration, detection
  parameters, laptop-only recording, FrED connection.
- *Experiment (FrED)* — the experiment settings and the Send / Abort /
  Retrieve Data buttons.

**Keys:** F11 = fullscreen, Esc = leave fullscreen.

---

## 4. Camera, speed and the measurement pipeline

### How frames flow

```
camera ──► capture thread ──► queue (max 8) ──► measure thread ──► measurement log
            stamps each frame                    detector runs on     (every frame,
            with the laptop clock                EVERY frame          laptop clock)
            the moment it arrives                                         │
                                                                          ├─► live graph + reading (10×/s)
            GUI thread ◄── one rendered view per display refresh          │
            (shows the selected feed, ~30×/s)                             └─► merge on Retrieve Data
```

- **Capture thread**: grabs frames as fast as the camera delivers them and
  timestamps each one immediately (`time.perf_counter`), so timestamps are not
  delayed by processing or drawing.
- **Measure thread**: runs the detector on every frame, in order. Overlays are
  drawn only for the frames that will actually be displayed.
- **GUI thread**: only displays results. Before v7 the GUI thread did
  everything in sequence (read, measure, draw two feeds), fell behind the
  camera and ended up reading stale, buffered frames.
- If measuring ever falls behind, the oldest queued frames are dropped (and
  counted) instead of letting the delay grow.

### Speed

| Frame size | v6 detector (with drawing) | v7 measure only | v7 with drawing (displayed frames) |
|---|---|---|---|
| 640 × 480 | 3.4 ms | **1.6 ms** | 2.9 ms |
| 1280 × 720 | 9.2 ms | **4.7 ms** | 6.5 ms |
| 1920 × 1080 | 20.6 ms | **9.7 ms** | 14.1 ms |

At 640 × 480 the detector could handle hundreds of frames per second, so the
**camera is the limit**. The laptop's current camera delivers ~30 fps in every
mode we tried (it ignores requests for MJPG / 60 fps). With a faster USB
camera, v7 measures at that camera's full rate automatically.

### Camera settings (Measure & Connect → Camera)

| Setting | Meaning |
|---|---|
| **Index** | which camera (0 = first). Change it and press **Reconnect**. |
| **Resolution** | *Camera default*, 640×480, 1280×720 or 1920×1080. Higher resolution = more pixels across the fiber (finer diameter steps) but more time per frame. |
| **High-FPS mode (MJPG)** | asks the camera for compressed MJPG frames at up to 120 fps. Only helps if the camera supports it; compression can add noise at the fiber edges. |
| **Reconnect** | applies the settings above (not allowed while a laptop-only recording runs). |

---

## 5. Calibration and detection parameters

### Calibration (do it once per optical setup)

1. Put an object of **known diameter** in view (a gauge wire, or a fiber
   measured with a caliper) and make sure it is detected (green box).
2. **Calibrate (reference)** → enter the units (`mm`) → enter the true
   diameter. The pixel→unit factor is computed and saved to
   `calibration.json`, so it persists between sessions.
3. Alternatives: **Enter factor** (type a known units-per-pixel value) and
   **Clear** (back to pixels).

The graph and the experiment export always use **millimetres** when the
calibration unit converts to mm (`mm`, `um`/`µm`, `cm`, `m`, `in`). If the
camera is **not calibrated**, everything is in **pixels** and the export's
diameter headers say `(px)`. Send & Start warns you in that case.

### Detection parameters

| Parameter | Default | What it does |
|---|---|---|
| **Auto threshold (Otsu)** | on | picks the black/white cut-off automatically for each frame |
| **Threshold** | 110 | manual cut-off (0–255), used only when Otsu is off; raise it if the background leaks into the mask |
| **Blur** | 5 | Gaussian blur size before thresholding; larger = less sensor noise, softer edges |
| **Min area** | 500 px² | blobs smaller than this are ignored (outlined red in the mask) |
| **Reset to defaults** | — | restores the four values above |

Watch the **Mask (processed)** feed while tuning: the fiber should be one
clean white band.

---

## 6. Connecting to FrED

1. On the **Pi**: `bash setup_hotspot.sh` (once per boot) and start the Pi
   program. Its screen shows the Wi-Fi name / password and the IP + port.
2. On the **laptop**: join the Wi-Fi **`FrED_Pi`** (password `fredfiber123`).
3. *Measure & Connect* → **FrED connection (WiFi)**: IP `192.168.4.1`, port
   `5005` (pre-filled; type what the Pi shows if different) → **Connect**.

After connecting, the status reads e.g. `Connected to 192.168.4.1:5005 |
clock sync ±1.2 ms (40 pings)`. The laptop sends a burst of 10 sync pings at
once, then one every 2 s. `clock sync: waiting` means FrED is not answering
pings — it is probably running pre-v7 software (`git pull` on the Pi).

The Pi re-announces its current phase every time the laptop (re)connects, so
the laptop always knows where a run stands.

---

## 7. Running an experiment, step by step

**Before**
1. Camera running and **calibrated**; fiber detected (green box).
2. **Connected** to FrED; the sync status shows a ± value.
3. Fill in the *Experiment (FrED)* tab (section 8) and the save name / folder.

**Start** — one of two ways:
- **Send & Start Experiment**: FrED runs the timed sequence HEATING →
  HEATING + EXTRUSION → SETTLE → RECORDING → EXTRA SPOOLING → COMPLETE.
- **START RECORDING NOW** with nothing running: FrED starts the run
  **directly in RECORDING** (no heating phases) — e.g. after heating by hand.

**During**
- **START RECORDING NOW** — the moment the fiber drops. During heating,
  heating + extrusion or settle, FrED **skips the rest of the warm-up**:
  spooler, fan and stepper start and recording starts immediately (t = 0 is
  that instant), for the full data-taking time. Once FrED is already
  recording, the button is ignored (the status bar says so).
- **MARK STEADY STATE** — when you judge the flow steady. The export gets a
  `Steady state` column: 0 before the press, 1 from it on. Press again to
  move the mark. It does **not** change anything on FrED. You can press it
  before recording starts (then every row is 1).
- **ABORT** (run bar or tab) — asks for confirmation, then stops **every**
  system on FrED: heater, stepper, spooler, fan. With no run active it works
  as a remote all-stop. FrED's red STOP buttons do the same.
- FrED's screen is locked during the run (only its red STOP buttons work).

**After**
- **Retrieve Data** — available as soon as recording ends (you do not need to
  wait for the extra spooling). A dialog blocks the app while FrED sends its
  table (60 s timeout, Cancel allowed), then shows a progress bar while the
  merge runs and the three files are written (section 10).
- **Keep this app open, with the camera running, from the start of the run
  until Retrieve Data.** The diameter data lives in the app's memory until
  it is merged. If you try to quit before retrieving, the app warns you.

---

## 8. Experiment tab — every field

| Field | Unit | Default | Allowed | Used when |
|---|---|---|---|---|
| Heating time — heater only | s | 60 | ≥ 0 | HEATING phase length |
| Heating + extrusion time | s | 30 | ≥ 0 | HEATING + EXTRUSION phase length |
| Extrusion rate during it | RPM | 1.5 | ≥ 0 | stepper speed during HEATING + EXTRUSION only |
| Experiment settle time | s | 10 | ≥ 0 | SETTLE phase length (everything on, not recorded) |
| Data-taking time | s | 120 | > 0 | RECORDING length (also after START RECORDING NOW) |
| Extra spooling after end | s | 15 | ≥ 0 | spooler keeps coiling after recording; 0 = stop at once |
| **FrED sample rate** | Hz | 50 | 1–100 | FrED's control loops **and** data rows (one rate); 50 → ~46–48 Hz achieved |
| Heater mode | — | closed | closed / open | closed = setpoint + PID, open = fixed PWM |
| Target temp | °C | 95 | ≥ 0 | closed mode |
| Temp Kp / Ki / Kd | — | 1.0 / 0.001 / 0.05 | ≥ 0 | closed mode |
| Heater PWM | % | 0 | 0–100 | open mode |
| Spooler mode | — | closed | closed / open | closed = RPM setpoint + PID, open = fixed PWM |
| Setpoint | RPM | 30 | ≥ 0 | closed mode |
| **Motor Kp** | — | 0.5 | **0–1** | closed mode |
| **Motor Ki** | — | 0.5 | **0–15** | closed mode |
| **Motor Kd** | — | 0.05 | **0–0.05** | closed mode |
| DC Motor PWM | % | 0 | 0–100 | open mode |
| Extrusion speed | RPM | 1.5 | ≥ 0 | stepper from SETTLE on |
| Fan duty | % | 40 | 0–100 | fan from SETTLE on |
| Target diameter | mm | 0.35 | ≥ 0 | logged in the data + red line on the graph (no control uses it) |
| File name / Folder | — | `fred_experiment` / `Data/` | — | where Retrieve Data saves |

Every value is checked before sending; anything out of range is listed in one
message and nothing is sent. FrED also clamps the spooler gains to the same
limits.

---

## 9. Time synchronisation — how the two data sets are lined up

FrED (the Pi) and the laptop each have their own clock, and WiFi delays vary
from about a millisecond to hundreds of milliseconds. Using "the time a message
arrived" would shift the diameter against FrED's data by an unknown, varying
amount. v7 does this instead:

**1. Each machine timestamps its own samples, on its own monotonic clock, at
the moment of acquisition.**
- Laptop: the instant a camera frame arrives (`time.perf_counter`).
- FrED: the instant its control tick reads the sensors (`gui.now()`, a
  monotonic clock that never jumps when the system time changes).

**2. The clock offset is measured continuously (the NTP method).** Every 2 s
the laptop sends a ping. Four timestamps are recorded:

```
t1  laptop sends the ping        (laptop clock)
t2  FrED receives it             (FrED clock)
t3  FrED sends the reply         (FrED clock)
t4  laptop receives the reply    (laptop clock)

offset = ((t2 − t1) + (t3 − t4)) / 2      FrED clock minus laptop clock
delay  =  (t4 − t1) − (t3 − t2)           network round trip
```

The offset estimate is wrong by at most half the round trip, and network
queuing only ever *adds* delay — so the **fastest exchange of every 10 s** is
kept and the rest discarded. A **straight line** is fitted through those
points (once they span ≥ 30 s) to follow the slow drift between the two clock
crystals (typically a few ppm, i.e. a few ms per 10 minutes). Each Pi session
has an id, so pings from an earlier Pi run are never mixed in.

**3. A common t = 0.** t = 0 is the instant FrED starts RECORDING (by its
timer or by START RECORDING NOW). FrED records that instant on its own clock
and sends it with the data; the laptop maps it onto the laptop clock with the
fitted line. Both data sets then start at the same instant.

**4. Each stream keeps its own rate.** FrED writes one row per control tick
(e.g. ~46 Hz); the camera delivers ~30 fps. Each FrED row gets the **latest
camera frame taken at or before the row's time** — never interpolated, never
from the future — and the flags in section 10 say which rows carry a new
frame and which repeat the previous one. The full-rate camera data keeps every
frame's own exact time.

### Error budget

| Source | Typical size | How it is handled |
|---|---|---|
| Clock-sync error | ~0.1 ms on loopback, a few ms on the WiFi hotspot | measured continuously; the estimate is written in *Run info* |
| Camera delay (exposure → delivery) | about one frame (~33 ms), constant | **not compensated** by default; set `CAMERA_LATENCY_S` if you measure it |
| Holding the latest frame on a FrED row | 0–33 ms at 30 fps | `Diameter new frame` flag; exact frame times in the camera sheet |
| Spacing between FrED rows | ~21 ms at the 50 Hz setting | every row has its exact time |
| FrED sensor read vs. row time | < 1 ms | read in the same tick as the row |
| Steady-state mark | the operator's reaction time (a few tenths of a second) | a human judgement; the mark is the button-press time |
| Live-graph filter lag | ~0.1 s | graph only; the export uses a centred filter with **no** lag |

---

## 10. Exported files — every column

Retrieve Data writes three files into the chosen folder, named after the
experiment.

### `<name>.csv` — FrED's table with the diameter merged in

Semicolon-delimited, **comma decimals** (so Excel set to Spanish/Mexico opens
it directly), one row per FrED sample tick during RECORDING.

| # | Column | Meaning |
|---|---|---|
| 1 | `Time (s)` | seconds since FrED started recording (t = 0) |
| 2 | `Temperature (C)` | thermistor reading taken in this tick |
| 3 | `Temp setpoint (C)` | heater target (0 in open-loop mode) |
| 4 | `Temp error (C)` | setpoint − controlled temperature (0 in open loop) |
| 5 | `Temp PID output` | heater duty sent (%) |
| 6–8 | `Temp Kp`, `Temp Ki`, `Temp Kd` | gains in use (0 in open loop) |
| 9 | `Temp new reading` | **1** = fresh thermistor reading in this row, **0** = repeated value |
| 10 | `Diameter (mm)` | **filtered** diameter of the camera frame held by this row |
| 11 | `Diameter raw (mm)` | that frame's diameter exactly as measured |
| 12 | `Diameter new frame` | **1** = this camera frame appears here for the first time, **0** = a repeat that only fills the row |
| 13 | `Diameter camera frame #` | which camera frame (look it up in `<name>_camera.csv`) |
| 14 | `Diameter setpoint (mm)` | target diameter of the run |
| 15 | `Fan duty (%)` | fan duty |
| 16 | `Extruder RPM` | stepper speed |
| 17 | `Spooler setpoint (RPM)` | spooler target (0 in open loop) |
| 18 | `Spooler RPM` | spooler speed (measured over the last 0.1 s) |
| 19–21 | `Spooler Kp`, `Spooler Ki`, `Spooler Kd` | gains in use |
| 22 | `Spooler new reading` | **1** = fresh encoder reading in this row, **0** = repeated value |
| 23 | `Steady state` | **0** before MARK STEADY STATE, **1** from it on |

Notes:
- Diameter cells are **blank** when the held frame found no fiber, or when the
  newest frame is more than 0.5 s old (camera stopped).
- In v7 FrED's control and logging share one tick, so `Temp new reading` and
  `Spooler new reading` are 1 in practically every row (the first row after a
  START RECORDING NOW can be 0 — the loops had just run).
- If the camera is not calibrated, columns 10–11 are `Diameter (px)` /
  `Diameter raw (px)`.
- **Using the flags in Excel:** filter `Diameter new frame = 1` to get one row
  per real camera measurement; filter `Steady state = 1` for the steady part
  of the run.

### `<name>_camera.csv` — every camera frame at full rate

Same format and the same t = 0. It starts with the frame held by the first
FrED row, so its first time can be a few ms below 0.

| Column | Meaning |
|---|---|
| `Time (s)` | frame arrival time since FrED recording start |
| `Camera frame #` | frame number (matches column 13 above) |
| `Fiber detected` | 1 / 0 |
| `Diameter (mm)` | filtered diameter |
| `Diameter raw (mm)` | measured diameter |
| `Diameter (px)` | measured diameter in pixels |
| `Min (px)`, `Max (px)`, `Std (px)` | variation of the thickness along the fiber in this frame |
| `Steady state` | 0 / 1, as above |

### `<name>.xlsx` — formatted Excel copy

- Sheet **FrED Experiment** — the merged table with bold white headers on a
  colour per subsystem (time grey, temperature red, diameter blue, fan teal,
  extruder purple, spooler green, steady state orange), the top row frozen,
  and three native Excel charts next to the data:
  **Diameter** (filtered + raw from the full-rate camera data, plus the
  setpoint), **Temperature** (+ setpoint) and **DC Spooling Motor** (+ setpoint).
- Sheet **Camera (full rate)** — the camera CSV.
- Sheet **Run info** — experiment name and save time; what t = 0 is (timer or
  START RECORDING NOW); the time-alignment method; clock-sync pings, offset,
  drift (ppm) and **estimated alignment error (ms)**; FrED rows and achieved
  rate; recording duration; camera frames, fps and detections; rows with a
  diameter / with a new frame; diameter unit; steady-state time; explanations
  of the flag columns and the filter; the camera-latency setting; and every
  experiment parameter.

### Diameter filter

Median over 5 detected frames (removes single-frame spikes such as
mis-detections), then mean over 3 (smooths the remaining jitter). The export
applies it **centred** on each frame, so it adds **no time lag**. The live
graph can only use past frames, so its filtered line trails by ~0.1 s.
Undetected frames never enter the filter.

---

## 11. Recording on the laptop only (without FrED)

*Measure & Connect* → **Recording (this laptop only)** records the diameter
alone:

- **File name**, **Save folder** (**Change...** opens the file manager).
- **Samples to record**: 0 = unlimited; otherwise recording stops by itself
  after that many samples and offers to save.
- **Start** → every frame with a detected fiber adds one row.
- **Pause & Save** → asks to confirm; *No* lets you discard or keep the data.
- **Save As...** → save anywhere without ending the recording.

Writes `<name>.csv` + `<name>.xlsx` with one row per frame:

| Column | Meaning |
|---|---|
| `timestamp` | wall-clock time the frame arrived |
| `elapsed_s` | seconds since Start |
| `frame` | running sample number |
| `diameter_px` | median fiber diameter, in pixels |
| `diameter_real` | diameter in calibrated units (blank if not calibrated) |
| `units` | unit string (`mm`, `um`, …) or `px` |
| `min_px`, `max_px`, `std_px` | variation along the fiber, in pixels |
| `length_px` | detected fiber length, in pixels |
| `angle_deg` | fiber tilt angle |

---

## 12. How the diameter is measured

`FiberDetector` in `fiber_measure.py`, for every frame:

1. Convert to greyscale and apply a **Gaussian blur**.
2. **Threshold** (Otsu automatic, or manual) → white fiber on black.
3. **Morphological clean-up**: opening removes specks, closing fills small gaps.
4. Take the **largest contour** (ignored if smaller than *Min area*) and fit a
   **minimum-area rectangle** → the fiber's centre, length and tilt.
5. Rotate **only the fiber's strip** (that rectangle plus a small margin) so
   the fiber is horizontal. v6 rotated the whole frame; the result is
   pixel-for-pixel identical (verified on 72 synthetic images).
6. Count the white pixels in every column → a thickness profile along the
   fiber. Drop the outer 10 % at each end (tapered tips).
7. **Diameter = median** of the profile; min / max / std describe the
   variation along the fiber in that frame.

Each column's thickness is a whole number of pixels, so the median moves in
steps of about half a pixel. Higher camera resolution or more magnification
gives finer steps.

---

## 13. Code map and tuning constants

| Item in `fiber_measure.py` | Role |
|---|---|
| `Calibration` | pixel → unit factor, saved in `calibration.json` |
| `FredLink` | WiFi client: sends commands, reads FrED's replies on a background thread |
| `ClockSync` | the NTP-style offset / drift estimate (section 9) |
| `FiberDetector` | the measurement (section 12) |
| `CameraWorker` | capture + measure threads (section 4) |
| `MeasurementLog` | every measured frame on the laptop clock |
| `LivePlot` | the diameter graph (plain Tk canvas; a redraw costs ~1–2 ms) |
| `parse_fred_csv`, `merge_run`, `write_run_xlsx` | the export (section 10) |
| `FiberApp` | the window and all buttons |

| Constant | Default | Meaning |
|---|---|---|
| `CAMERA_LATENCY_S` | 0.0 | shift camera times earlier by the camera's own delay |
| `CAMERA_RESOLUTIONS` | 4 options | the Resolution list |
| `FILTER_MEDIAN`, `FILTER_MEAN` | 5, 3 | diameter filter windows (frames) |
| `SYNC_INTERVAL_S` | 2.0 | clock-sync ping period |
| `STALE_FRAME_S` | 0.5 | a FrED row gets no diameter if the newest frame is older |
| `MOTOR_GAIN_LIMITS` | (1, 15, 0.05) | spooler Kp / Ki / Kd limits (must match the Pi) |
| `SAMPLE_RATE_LIMITS` | (1, 100) | allowed FrED sample rates (Hz) |
| `ClockSync.BIN_S`, `ClockSync.FIT_MIN_SPAN_S` | 10 s, 30 s | fastest-ping bin width; span needed before the drift fit |
| `CameraWorker.QUEUE_FRAMES` | 8 | frames that may wait for measuring before the oldest is dropped |
| `FiberApp.LOG_KEEP_S` | 30 min | camera history kept when no experiment needs it (the last experiment's data is always kept) |
| `FiberApp.RETRIEVE_TIMEOUT_S` | 60 s | Retrieve Data gives up after this |

---

## 14. Test results

Measured on the development laptop (camera: 640 × 480, ~30 fps):

| Test | Result |
|---|---|
| Frames measured per second, v6 → v7 | 21.4 → **29.8** (every frame, 0 dropped) |
| Detector v7 vs v6 on 72 synthetic fibers (2 resolutions, 12 angles, 3 widths) | identical diameters, ~2× faster |
| Clock sync, simulated spiky + asymmetric WiFi delays, 40 ppm drift | recording start placed within **0.3 ms**; drift recovered as 40.0 ppm |
| Same simulation, naive "arrival time" method | ~7 ms off on average, up to 300 ms |
| End to end: real Pi program (simulated hardware) + this app (simulated camera whose fiber width changes at known instants) | t = 0 within **0.02–0.05 ms** of the truth; **every** row showed the width the camera saw at that instant; START RECORDING NOW both skipped a 30 s settle and started a run from idle; the steady-state column switched at the pressed time; a Ki of 20 was refused and 12.5 accepted |

**Not yet verified on the real machine:** sync quality over the real hotspot
(shown live in the status and in every file's *Run info*).

---

## 15. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Camera: not available` | wrong **Index**, or another program is using the camera; fix and **Reconnect** |
| *measured* fps below camera fps, or *dropped* growing | resolution too high for this laptop (1080p ≈ 10 ms/frame) or the laptop is busy; lower the resolution |
| Sync status stays `clock sync: waiting` | FrED is not answering pings: update the Pi (`git pull`, restart) |
| Retrieve note "FrED is running older software" | the Pi is on pre-v7 code; the file is saved as FrED sent it, without the camera merge — `git pull` on the Pi |
| Diameter columns empty | the app was closed/restarted after the run, the camera was not running, or no timing could be established — *Run info* → *Time alignment* says which |
| Diameter in px instead of mm | calibrate (section 5) |
| START RECORDING NOW "ignored" | FrED was already recording or spooling |
| Retrieve Data times out after 60 s | the link is reset automatically; click Retrieve Data again |
| "Excel not written" | install `openpyxl` (`pip install openpyxl`) |
| Values rejected when sending | the message lists each field and its allowed range (section 8) |

---

## Files

- `fiber_measure.py` — the application.
- `Run FrED Fiber Measure.bat` — double-click launcher.
- `calibration.json` — saved calibration.
- `Data/` — default output folder.
- `setup_install.py`, `setup_install.bat`, `requirements.txt` — installer.
