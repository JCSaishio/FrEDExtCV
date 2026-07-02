# FrED Fiber Measure — WiFi Streaming (v3)

Real-time fiber **diameter measurement** from a USB camera, with calibration,
CSV data logging, **and live streaming of the diameter to a FrED Raspberry Pi
over WiFi**. Built for a bright fiber imaged against a dark background (as in
`Example/`).

This is the companion to the `fred-device-external-cv-pi4v3` Raspberry Pi code:
the diameter is measured here, on your computer, and streamed wirelessly to the
Pi, which graphs it in place of its old on-board camera feed.

> **v3 — wireless link.** Earlier versions streamed over a USB serial cable.
> Now the **Pi runs its own WiFi hotspot** (`FrED_Pi`); this laptop joins it and
> connects to the Pi by IP address — no cable, and no university network needed.

## What it does

- Live video from a USB camera with **border/edge detection** of the fiber.
- Robust diameter measurement that tolerates a **tilted** fiber and reports
  variation along its length (median diameter + min / max / std).
- **Calibration** so readings are in real units (mm, µm, …) instead of pixels.
- **Start / Pause** an experiment at will; each captured frame is one data row.
- On Pause, it asks for **confirmation** and then writes a timestamped `.csv`
  into the `Data/` folder.
- **Stream to FrED Pi (WiFi)**: connect to the Pi by IP address and stream each
  measurement to it as newline-delimited JSON over a TCP socket.

## Streaming to the FrED Pi (over WiFi)

1. On the **Pi**, start the hotspot once (`bash setup_hotspot.sh`) and run the
   Pi program. Its screen shows the Wi-Fi name/password and the IP + port to use.
2. On this **laptop**, join the Wi-Fi network **`FrED_Pi`**
   (password **`fredfiber123`**).
3. **Calibrate first** (see below) so the streamed diameter is in **mm** — the
   Pi's targets and PID expect millimetres (0.30–0.60 mm range). If you stream
   while uncalibrated, values are sent in pixels (`"u": "px"`).
4. In the **Stream to FrED Pi (WiFi)** panel: the **Pi IP** and **Port** are
   pre-filled with `192.168.4.1` and `5005` (the hotspot defaults). If the Pi's
   screen shows a different IP, type that one instead. Click **Connect**, then
   **Start streaming**.
5. On the Pi, press **Start Diameter/Camera Loop** to begin graphing.

Each measurement is sent (throttled to ~20 Hz) as one JSON line over TCP:

```json
{"v": 1, "d": 0.352, "u": "mm", "t": 12.345, "found": true}
```

Streaming and CSV recording are independent — you can do either or both.

## Experiment (FrED) tab — run a whole experiment remotely (v6)

The **Experiment (FrED)** tab sends a complete experiment to FrED, which runs it
automatically and sends the data back. Connect on the **Measure & Stream** tab
first (the diameter is streamed from this laptop, so keep the camera running and
calibrated).

Fill in:
- **Timing (s):** *heating delay* (heat only), *data delay* (after the other
  parameters switch on), and *data-taking time*.
- **Heater** and **Spooler:** pick **closed** (setpoint + PID gains) or **open**
  (raw PWM) mode and enter the matching values.
- **Extruder / Fan / Diameter:** stepper speed (RPM), fan duty (%), target
  diameter (mm).
- **Save:** the file name and folder for the returned data.

Then:
1. **Send & Start Experiment** — FrED heats, then activates everything, then
   records. The status line (and FrED's screen) shows the phase and time left.
   *(Diameter streaming is auto-enabled so it gets recorded.)*
2. When it finishes, click **Retrieve Data** — FrED sends the recorded CSV and
   it is saved as **`<name>.csv`** (FrED's three-table format) **and**
   **`<name>.xlsx`** in the chosen folder.
3. **Abort** stops a running experiment at any time (the red STOP buttons on
   FrED also abort it).

## Setup

Python is run through your Anaconda install. Install the missing packages:

```bash
"C:/Users/saish/anaconda3/python.exe" -m pip install -r requirements.txt
```

(`numpy` and `Pillow` are already present; this mainly adds `opencv-python` and
**`openpyxl`** for the Excel export. The WiFi streaming uses Python's built-in
`socket` module, so no extra streaming package is needed.)

There is also a one-call installer in this folder:

```bash
"C:/Users/saish/anaconda3/python.exe" setup_install.py
```

(or double-click `setup_install.bat` on Windows).

## Run

```bash
"C:/Users/saish/anaconda3/python.exe" fiber_measure.py
```

## Window & video

- The window is **fully resizable** — maximise it (or press **F11** for true
  fullscreen, **Esc** to leave) and the whole interface fills the screen. Both
  video feeds scale to fill the available space.
- There are **two video feeds, stacked**:
  - **Live (detected)** (top) — the camera image with the detected fiber box and
    the diameter tick drawn on it.
  - **Processed (mask / edge detection)** (bottom) — the binary image after blur
    / threshold / morphology, i.e. exactly where detection happens. Adjust the
    **Detection parameters** and watch this feed to see their effect (a rejected
    too-small blob is outlined in red so the **Min area** effect is visible).
- The control panel on the right sizes itself to its content (never clipped) and
  shows a scrollbar **only when** the window is too short to fit all controls.

## How to use

1. **Connect the camera.** It opens index `0` by default. If you have several
   cameras, change the **Index** and click **Reconnect**.
2. **Tune detection** (only if needed). The defaults (auto Otsu threshold,
   blur 5, min area 500) work for a bright fiber on a dark background. Watch the
   **Processed** feed; the fiber should appear as a clean white band. If the
   background leaks in, untick *Auto threshold* and raise the **Threshold** slider.
3. **Calibrate** (do this once per optical setup):
   - Place an object of **known** diameter in view (e.g. a gauge wire, or the
     fiber after measuring it with a caliper) and make sure it is detected.
   - Click **Calibrate (reference)**, enter the units (e.g. `mm`) and the true
     diameter. The pixel→unit factor is computed and saved to
     `calibration.json`, so it persists between sessions.
   - Alternatively, **Enter factor** lets you type a known units-per-pixel
     value directly. **Clear** removes calibration (readings revert to pixels).
4. **Run an experiment:**
   - **File name** — type the name for the saved files (no extension needed).
   - **Save folder** — defaults to `Data/`; click **Change...** to pick any
     folder via the Windows file manager.
   - **Samples to record** — set how many samples this experiment should
     capture. `0` means **unlimited** (record until you press Pause & Save, the
     original behaviour). With a positive number, recording **auto-stops** once
     that many samples are reached and offers to save; afterwards the Start /
     Pause & Save / Save As buttons work exactly as before.
   - Click **Start** to record; every frame with a detected fiber adds a sample.
   - Click **Pause & Save** to stop and confirm. On **Yes**, the data is written
     as **both** `<name>.csv` **and** `<name>.xlsx` into the save folder.
     On **No**, you can discard or keep the data.
   - **Save As...** opens the Windows save dialog so you can write the current
     data (again, both `.csv` and `.xlsx`) to any location/name you choose,
     without ending the experiment.

## CSV / Excel format

Each save writes a `.csv` and an `.xlsx` with the same name; both have one row
per recorded frame with identical columns:

| column | meaning |
|---|---|
| `timestamp` | wall-clock time of the sample |
| `elapsed_s` | seconds since the experiment started |
| `frame` | running sample number |
| `diameter_px` | median fiber diameter, in pixels |
| `diameter_real` | diameter in calibrated units (blank if not calibrated) |
| `units` | unit string (`mm`, `um`, …) or `px` |
| `min_px`, `max_px`, `std_px` | diameter variation along the fiber, in pixels |
| `length_px` | detected fiber length, in pixels |
| `angle_deg` | fiber tilt angle |

## How the measurement works

`fiber_measure.py` → `FiberDetector`:

1. Convert to grayscale and **Gaussian blur** to suppress sensor noise.
2. **Threshold** (Otsu auto, or manual) to isolate the bright fiber.
3. **Morphological** open/close to remove specks and fill small gaps.
4. Take the **largest contour** and fit a `minAreaRect` to get the fiber's
   orientation.
5. **Rotate** the mask so the fiber is horizontal, then measure the white-pixel
   thickness of every column. The ends are trimmed (10%) to avoid tapered tips.
   The **median** column thickness is the reported diameter; min/max/std capture
   the variation.

## Files

- `fiber_measure.py` — the application.
- `calibration.json` — saved calibration (created on first calibrate).
- `Data/` — output CSV files (created on first save).
- `Example/` — sample fiber image.
