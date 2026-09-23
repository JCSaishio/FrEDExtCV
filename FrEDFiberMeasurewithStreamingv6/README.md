# FrED Fiber Measure — v7 (diameter measured on this laptop)

Real-time fiber **diameter measurement** from a USB camera, with calibration,
a **live diameter graph**, CSV/Excel logging, and **remote control of automated
FrED experiments over WiFi**. Built for a bright fiber imaged against a dark
background.

This is the companion to the `fred-device-extcv-pi4v6` Raspberry Pi code.

> **v7 — the diameter stays on the laptop.** The Pi no longer receives,
> filters or graphs the diameter (that load was hurting its PID loops). This
> app measures every camera frame, graphs it, and records it on its own clock.
> The WiFi link to FrED only carries experiment commands and a tiny clock-sync
> ping. When you click **Retrieve Data**, FrED's temperature/spooler table and
> this laptop's diameter data are merged onto **one timeline** into the same
> CSV + Excel files as before.

## What's new in v7

- **Every camera frame is measured.** Frames are grabbed on one background
  thread (timestamped the instant they arrive) and measured on another; the
  window only displays results. On the development laptop's camera the old
  app measured **21.4 frames/s**; v7 measures all **29.8 frames/s** the camera
  delivers, with 0 dropped. The detector itself is ~2× faster (it rotates only
  the fiber's own strip, not the whole frame) and gives identical diameters.
- **Live diameter graph** under the video (raw + filtered, target line,
  recording-start and steady-state markers).
- **START RECORDING NOW** button — press it the moment the fiber drops.
- **MARK STEADY STATE** button — flags the data from the moment you judge the
  flow steady (it changes nothing else).
- **FrED sample rate** per experiment (control AND data rate on FrED).
- Spooler PID gains limited to **Kp ≤ 1, Ki ≤ 15, Kd ≤ 0.05**.
- Clock-synchronised merge with "new frame / new reading" flags so repeated
  values can always be told apart from real measurements.

## Window layout

```
┌──────────────────────────────────┬───────────────────┐
│ [Live feed] [Mask (processed)]   │ Measure & Connect │
│  camera feed (switchable)        │ Experiment (FrED) │
├──────────────────────────────────┤   (tabs)          │
│  Diameter vs time (always shown) │                   │
├──────────────────────────────────┤                   │
│ START RECORDING NOW | MARK STEADY STATE |  ABORT     │
│ FrED: phase (time left) | steady mark | sync ±ms     │
└──────────────────────────────────┴───────────────────┘
```

- The top area shows **one** feed at a time — **Live feed** (camera image with
  the detected box and diameter tick) or **Mask (processed)** (the binary
  image after blur / threshold / morphology, where detection happens). Only
  the visible feed is drawn, which saves CPU.
- The **diameter graph** is always visible: light blue = every frame (raw),
  dark blue = filtered, dashed red = target diameter (from the Experiment tab),
  green line = FrED recording start (t = 0), orange line = steady-state mark.
  **Show last** picks the time window (10 s … 5 min, or the whole run). Once
  FrED starts recording, the x-axis is *time since recording start*, matching
  the exported files.
- The frame-rate line above the video shows the camera rate, the measured
  rate and dropped frames (should stay 0).
- F11 = fullscreen, Esc = leave fullscreen.

## Connecting to FrED

1. On the **Pi**: `bash setup_hotspot.sh` (once per boot) and run the Pi
   program. Its screen shows the Wi-Fi name/password and the IP + port.
2. On this **laptop**: join the Wi-Fi **`FrED_Pi`** (password
   **`fredfiber123`**).
3. **Measure & Connect** tab → **FrED connection (WiFi)**: IP `192.168.4.1`,
   port `5005` (pre-filled) → **Connect**. The status line then shows the
   clock-sync quality, e.g. `clock sync ±1.2 ms (40 pings)`.

## Experiment (FrED) tab

Fill in:

- **Experiment sequence:** *heating time* (heater only), *heating + extrusion
  time* with its **own extrusion rate (RPM)**, *settle time* (all systems on
  before recording), *data-taking time*, *extra spooling after end*, and the
  **FrED sample rate (Hz, 1–100)** — the rate of FrED's temperature and
  spooler control loops *and* of its recorded rows (one rate for both; 50 Hz
  gives ~46–48 Hz actual, the achieved rate is written to the Run info sheet).
- **Heater** and **Spooler:** **closed** (setpoint + PID) or **open** (raw PWM).
  Spooler gains must stay within **Kp 0–1, Ki 0–15, Kd 0–0.05** (FrED enforces
  the same limits).
- **Extruder / Fan / Diameter:** stepper speed, fan duty, target diameter.
- **Save:** file name and folder for the returned data.

Then:

1. **Send & Start Experiment** — FrED heats → heats + extrudes → activates
   everything (settle) → records → keeps only the spooler running for the
   extra spooling time. FrED's screen is locked except its red STOP buttons.
2. **START RECORDING NOW** (under the graph) — when the fiber drops:
   - during heating / extrusion / settle, FrED **skips the rest of the
     warm-up**: spooler, fan and stepper start and recording starts at once
     (t = 0 is that instant), for the full data-taking time;
   - with **no experiment running**, it starts the Experiment tab's run
     **directly in recording** (no heating phases) — e.g. after heating by hand.
   It is ignored once FrED is already recording.
3. **MARK STEADY STATE** — press when you see the flow has become steady.
   It only marks the data: the export gets a **`Steady state`** column (0
   before the mark, 1 from it on) and the mark time in *Run info*. Pressing
   again moves the mark. It has no effect on FrED.
4. **Retrieve Data** (any time after recording ends) — a progress dialog
   blocks the app while FrED sends its table, the merge runs and the files are
   written.
5. **ABORT** stops every system on FrED (heater, stepper, spooler, fan). With
   no run active it is a remote all-stop.

Keep this app open (camera running) from the start of the run until you have
clicked **Retrieve Data**: the diameter data lives here until it is merged.

## Time synchronisation (how the two data sets are lined up)

FrED and the laptop each have their own clock, and WiFi delays vary from a
millisecond to hundreds of ms, so "when a message arrived" is not good enough.
The method:

1. **Each machine timestamps its own samples** with its own monotonic clock at
   acquisition: the laptop when a camera frame arrives, FrED when its control
   tick reads the sensors.
2. **Clock sync (NTP method).** While connected, the laptop pings FrED every
   2 s. Each ping records 4 timestamps (sent/received on both sides), giving
   the clock offset with an error of at most half the round trip. Only the
   fastest exchange of every 10 s is used (congestion only ever adds delay),
   and a straight line fitted through them removes the slow drift between the
   two clock crystals.
3. **Common t = 0.** FrED reports the exact instant it started recording
   (timer or START RECORDING NOW) on its own clock; the laptop converts it to
   the laptop clock. Both data sets then start at the same instant.
4. **Each stream keeps its own rate.** FrED writes rows at its sample rate;
   the camera delivers ~30 fps. Each FrED row gets the **latest camera frame at
   or before its time** (never interpolated, never from the future).

Tested end to end (real FrED program with simulated hardware + this app with a
simulated camera): t = 0 placed within **0.05 ms** of the truth, and every row
showed the thickness the camera saw at that instant. In a simulation with
spiky, asymmetric WiFi delays and 40 ppm drift, the error stayed under 0.3 ms
(the naive "arrival time" method would be off by ~7 ms on average and up to
300 ms). On the real hotspot expect a few ms; each file's *Run info* sheet
states the estimated error.

Remaining known offset: the camera's own delay between exposure and delivery
(typically about one frame, ~33 ms) is not compensated by default. If you
measure it, set `CAMERA_LATENCY_S` at the top of `fiber_measure.py`.

## Experiment export format

Retrieve Data writes three files into the chosen folder:

**`<name>.csv`** — FrED's table with the diameter merged in (semicolon
delimiter, comma decimals for Excel es-MX), one row per FrED sample:

| column(s) | meaning |
|---|---|
| `Time (s)` | seconds since FrED started recording (t = 0) |
| `Temperature (C)` … `Temp Kd` | heater data (as before) |
| `Temp new reading` | **1** = a fresh thermistor reading in this row, **0** = repeated |
| `Diameter (mm)` | filtered diameter of the camera frame held by this row |
| `Diameter raw (mm)` | that frame's diameter exactly as measured |
| `Diameter new frame` | **1** = this frame appears here for the first time, **0** = repeat that only fills the row |
| `Diameter camera frame #` | which camera frame (see `<name>_camera.csv`) |
| `Diameter setpoint (mm)`, `Fan duty (%)`, `Extruder RPM` | as before |
| `Spooler setpoint (RPM)` … `Spooler Kd` | spooler data (as before) |
| `Spooler new reading` | **1** = a fresh encoder reading in this row, **0** = repeated |
| `Steady state` | **0** before the MARK STEADY STATE press, **1** from it on |

Diameter cells are blank when that frame found no fiber, or when the newest
frame is more than 0.5 s old (camera stopped). If the camera is not calibrated
the diameter columns are in `px` (their headers say so).

**`<name>_camera.csv`** — every camera frame of the recording at full rate:
time (same t = 0), frame #, fiber detected, filtered / raw diameter, diameter
and min / max / std in px, steady state.

**`<name>.xlsx`** — sheets *FrED Experiment* (the merged table, colored bold
headers, frozen top row, and native charts of Diameter — from the full-rate
camera data — Temperature and DC Spooling Motor vs time), *Camera (full rate)*
and *Run info* (t = 0 source, sync offset / drift / estimated error, rates,
frame counts, steady-state time, all experiment parameters).

**Diameter filter:** a median over 5 detected frames (removes single-frame
spikes) followed by a mean over 3. The export applies it **centred**, so it
adds no time lag; the live graph applies it trailing (it cannot see the
future).

## Setup

Python is run through your Anaconda install:

```bash
"C:/Users/saish/anaconda3/python.exe" -m pip install -r requirements.txt
```

(`opencv-python`, `numpy`, `Pillow`, `openpyxl`; the WiFi link uses Python's
built-in `socket`, and the graph is plain Tk, so nothing else is needed.) Or
run `setup_install.py` / double-click `setup_install.bat`.

## Run

Double-click **`Run FrED Fiber Measure.bat`** (no console window; the file can
be copied anywhere, e.g. the Desktop). Or:

```bash
"C:/Users/saish/anaconda3/python.exe" fiber_measure.py
```

## How to use (Measure & Connect tab)

1. **Camera.** Opens index `0`. Change **Index** / **Resolution** / **High-FPS
   mode (MJPG)** and click **Reconnect**. High-FPS asks the camera for
   compressed frames: more frames per second *if the camera supports it*, but
   the compression can add noise at the fiber edges. (The laptop's current
   camera tops out at 30 fps in every mode.)
2. **Tune detection** if needed (defaults: auto Otsu threshold, blur 5, min
   area 500). Switch the feed to **Mask (processed)** to watch the effect; a
   rejected too-small blob is outlined in red.
3. **Calibrate** once per optical setup: put an object of known diameter in
   view, **Calibrate (reference)**, enter units (`mm`) and the true diameter.
   Saved to `calibration.json`. **Enter factor** types a units-per-pixel value
   directly; **Clear** reverts to pixels.
4. **Recording (this laptop only)** — records the diameter alone, without
   FrED: file name, save folder, samples to record (0 = unlimited, otherwise
   auto-stop), **Start**, **Pause & Save** (asks to confirm), **Save As...**.
   Writes `<name>.csv` + `<name>.xlsx`:

| column | meaning |
|---|---|
| `timestamp` | wall-clock time the frame arrived |
| `elapsed_s` | seconds since Start |
| `frame` | running sample number |
| `diameter_px` | median fiber diameter, in pixels |
| `diameter_real` | diameter in calibrated units (blank if not calibrated) |
| `units` | unit string (`mm`, `um`, …) or `px` |
| `min_px`, `max_px`, `std_px` | diameter variation along the fiber, in pixels |
| `length_px` | detected fiber length, in pixels |
| `angle_deg` | fiber tilt angle |

## How the measurement works

`fiber_measure.py` → `FiberDetector`:

1. Grayscale + **Gaussian blur** to suppress sensor noise.
2. **Threshold** (Otsu auto, or manual) to isolate the bright fiber.
3. **Morphological** open/close to remove specks and fill small gaps.
4. Largest **contour** → `minAreaRect` gives the fiber's orientation.
5. Only the fiber's **strip** (its `minAreaRect` plus a margin) is rotated
   flat, then the white-pixel thickness of every column is counted; the ends
   are trimmed (10%). The **median** thickness is the diameter; min/max/std
   capture the variation. Overlays are drawn only on displayed frames.

`CameraWorker` runs the capture and measurement threads; `MeasurementLog`
keeps every frame (laptop clock); `ClockSync` holds the sync maths;
`merge_run()` builds the export.

## Files

- `fiber_measure.py` — the application.
- `Run FrED Fiber Measure.bat` — double-click launcher.
- `calibration.json` — saved calibration.
- `Data/` — default output folder.
- `setup_install.py` / `setup_install.bat` / `requirements.txt` — installer.
