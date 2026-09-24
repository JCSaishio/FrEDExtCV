#!/usr/bin/env python3
"""fred_terminal.py -- headless, terminal-only FrED experiment runner.

Runs a whole experiment on the Raspberry Pi with **no PyQt / no matplotlib**, so
all the CPU goes to control and acquisition instead of drawing graphs. The
experiment is sent from the laptop (the existing *FrED Fiber Measure* app, same
WiFi/TCP protocol) or started locally for a bench test; FrED reports the phase,
time left and live readings on the terminal, then returns the recorded CSV.

Design (what we discussed):
  * Control loop  @ 10 Hz  -- heater + spooler PID. Owns the actuators. The PID
    stays at the proven 10 Hz rate so the control behaves exactly as before.
  * Sampler loop  @ 50 Hz  -- the ONLY reader of the SPI sensors (encoder + ADC).
    It timestamps every sample, applies the EMA filter (signal_filter.py) and
    logs BOTH the raw and the filtered value of each signal. The control loop
    consumes the cached raw readings, so the two SPI users never collide.
  * Network       -- a TCP server (same JSON protocol as external_diameter.py):
    receives the diameter stream and the experiment/abort/get_data commands,
    and sends status + the recorded data back.

The recorded CSV keeps the column names the laptop's Excel exporter expects
("Time (s)", "Temperature (C)", "Diameter (mm)", "Spooler RPM", ...) plus the
extra "... raw" columns, so Retrieve Data still builds the charted .xlsx.

Run on the Pi:
    python fred_terminal.py                 # wait for the laptop, then run what it sends
    python fred_terminal.py --demo          # run a short built-in experiment (no laptop)
    python fred_terminal.py --sim --demo    # dry-run OFF the Pi (fake sensors/actuators)

SAFETY: this drives a real heater and motors. Check TEMP_MAX / the pin map below
match your machine before running, and never leave it unattended.
"""
import argparse
import base64
import csv
import io
import json
import math
import os
import random
import socket
import sys
import threading
import time
from collections import deque

from signal_filter import (EMAFilter, ALPHA_TEMPERATURE, ALPHA_SPOOLER_RPM,
                           ALPHA_DIAMETER, SAMPLE_RATE_HZ)

# --------------------------------------------------------------------------- #
# Rates
# --------------------------------------------------------------------------- #
SAMPLE_HZ = SAMPLE_RATE_HZ      # fast data acquisition (50 Hz) -- matches the filter presets
SAMPLE_DT = 1.0 / SAMPLE_HZ
CONTROL_HZ = 10.0               # PID control (kept at the proven, stable rate)
CONTROL_DT = 1.0 / CONTROL_HZ

# --------------------------------------------------------------------------- #
# Hotspot / network (must match external_diameter.py + the laptop app)
# --------------------------------------------------------------------------- #
STREAM_PORT = 5005
READ_TIMEOUT = 0.5
SEND_TIMEOUT = 30.0             # big payloads (the recorded CSV) need a long deadline

# --------------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------------- #
TEMP_MAX = 230.0                # deg C hard cap: heater forced OFF above this
TEMP_SENSOR_MIN = 1.0           # deg C below this the thermistor is likely disconnected

# Camera interlock: an experiment is held until a diameter measurement has been
# received within this many seconds (proof the camera stream is live).
CAMERA_FRESH_S = 3.0

# --------------------------------------------------------------------------- #
# Terminal colors (ANSI). The Pi terminal supports these; Windows 10+ needs a
# one-time enable (done in enable_ansi). Disable with --no-color.
# --------------------------------------------------------------------------- #
RESET = "\033[0m"; BOLD = "\033[1m"
RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
BLUE = "\033[34m"; MAGENTA = "\033[35m"; CYAN = "\033[36m"
WHITE = "\033[97m"; GREY = "\033[90m"


def enable_ansi():
    """Turn on ANSI escape processing on Windows terminals (no-op elsewhere)."""
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            pass


# =========================================================================== #
# Hardware abstraction: real Pi drivers, or a simulator for off-Pi dry runs.
# =========================================================================== #
class Hardware:
    """Direct FrED hardware access (pins/formulas from extruder.py / spooler.py).

    Only the sampler thread calls the SPI reads (read_encoder / read_voltage);
    the actuator setters are called from the control thread.
    """
    HEATER_PIN = 6
    STEP_DIR_PIN = 16
    STEP_PIN = 20
    MICROSTEP_M2_PIN = 22
    MICROSTEP_FACTOR = 16
    STEPS_PER_REV = 200
    SPOOLER_PWM_PIN = 5
    FAN_PIN = 13
    ENC_SS_PIN = 1
    PULSES_PER_REV = 4704

    # Thermistor (Steinhart-Hart), identical to Thermistor in extruder.py
    REF_T = 298.15
    R_REF = 100000.0
    BETA = 3977.0
    VSUP = 3.3
    RESISTOR = 100000.0

    def __init__(self, sim=False):
        self.sim = sim
        if sim:
            self._sim_init()
            return
        import RPi.GPIO as GPIO
        import spidev
        import board, busio, digitalio
        import adafruit_mcp3xxx.mcp3008 as MCP
        from adafruit_mcp3xxx.analog_in import AnalogIn
        self.GPIO = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        for pin in (self.HEATER_PIN, self.STEP_DIR_PIN, self.STEP_PIN,
                    self.MICROSTEP_M2_PIN, self.SPOOLER_PWM_PIN, self.FAN_PIN,
                    self.ENC_SS_PIN):
            GPIO.setup(pin, GPIO.OUT)
        GPIO.output(self.STEP_DIR_PIN, GPIO.HIGH)          # direction
        GPIO.output(self.MICROSTEP_M2_PIN, GPIO.HIGH)      # 1/16 microstepping
        GPIO.output(self.ENC_SS_PIN, GPIO.HIGH)
        self.heater_pwm = GPIO.PWM(self.HEATER_PIN, 1)
        self.heater_pwm.start(0)
        self.step_pwm = GPIO.PWM(self.STEP_PIN, 1000)
        self.step_pwm.start(0)
        self.spooler_pwm = GPIO.PWM(self.SPOOLER_PWM_PIN, 1000)
        self.spooler_pwm.start(0)
        self.fan_pwm = GPIO.PWM(self.FAN_PIN, 1000)
        self.fan_pwm.start(0)
        # ADC (thermistor) over SPI
        spi = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)
        cs = digitalio.DigitalInOut(board.D8)
        mcp = MCP.MCP3008(spi, cs)
        self._adc = AnalogIn(mcp, MCP.P0)
        # Encoder over its own SPI device + manual chip-select
        self._enc = spidev.SpiDev()
        self._enc.open(0, 0)
        self._enc.max_speed_hz = 50000
        self._enc_init()

    # ---- encoder (quadrature counter over SPI), same command bytes as spooler.py
    def _enc_init(self):
        self.GPIO.output(self.ENC_SS_PIN, self.GPIO.LOW)
        self._enc.xfer2([0x88, 0x03])
        self.GPIO.output(self.ENC_SS_PIN, self.GPIO.HIGH)
        self.GPIO.output(self.ENC_SS_PIN, self.GPIO.LOW)
        self._enc.xfer2([0x98, 0x00, 0x00, 0x00, 0x00])
        self.GPIO.output(self.ENC_SS_PIN, self.GPIO.HIGH)
        time.sleep(0.0001)
        self.GPIO.output(self.ENC_SS_PIN, self.GPIO.LOW)
        self._enc.xfer2([0xE0])
        self.GPIO.output(self.ENC_SS_PIN, self.GPIO.HIGH)

    def read_encoder(self):
        if self.sim:
            return self._sim_read_encoder()
        self.GPIO.output(self.ENC_SS_PIN, self.GPIO.LOW)
        self._enc.xfer2([0x60])
        c1 = self._enc.xfer2([0x00]); c2 = self._enc.xfer2([0x00])
        c3 = self._enc.xfer2([0x00]); c4 = self._enc.xfer2([0x00])
        self.GPIO.output(self.ENC_SS_PIN, self.GPIO.HIGH)
        return (c1[0] << 24) + (c2[0] << 16) + (c3[0] << 8) + c4[0]

    def read_voltage(self):
        if self.sim:
            return self._sim_read_voltage()
        return self._adc.voltage

    @classmethod
    def voltage_to_temp(cls, voltage):
        """Steinhart-Hart, single reading (filtering is done by the EMA)."""
        if voltage < 0.0001 or voltage >= cls.VSUP:
            return 0.0
        resistance = ((cls.VSUP - voltage) * cls.RESISTOR) / voltage
        if resistance <= 0:
            return 0.0
        try:
            ln = math.log(resistance / cls.R_REF)
            return (1.0 / ((ln / cls.BETA) + (1.0 / cls.REF_T))) - 273.15
        except (ValueError, ZeroDivisionError):
            return 0.0

    # ---- actuators
    def set_heater(self, duty):
        duty = max(0.0, min(100.0, duty))
        if self.sim:
            self._sim_heater = duty
        else:
            self.heater_pwm.ChangeDutyCycle(duty)

    def set_stepper_rpm(self, rpm):
        if self.sim:
            self._sim_stepper = rpm
            return
        if rpm <= 0.0:
            self.step_pwm.ChangeDutyCycle(0)
            return
        freq = (rpm * self.STEPS_PER_REV / 60.0) * self.MICROSTEP_FACTOR
        if freq > 0:
            self.step_pwm.ChangeFrequency(freq)
            self.step_pwm.ChangeDutyCycle(50)

    def set_spooler_duty(self, duty):
        duty = max(0.0, min(100.0, duty))
        if self.sim:
            self._sim_spooler_duty = duty
        else:
            self.spooler_pwm.ChangeDutyCycle(duty)

    def set_fan(self, duty):
        duty = max(0.0, min(100.0, duty))
        if self.sim:
            self._sim_fan = duty
        else:
            self.fan_pwm.ChangeDutyCycle(duty)

    def all_off(self):
        self.set_heater(0); self.set_stepper_rpm(0)
        self.set_spooler_duty(0); self.set_fan(0)

    def cleanup(self):
        self.all_off()
        if not self.sim:
            try:
                self.GPIO.cleanup()
            except Exception:
                pass

    # ---- simulator (only used with --sim, so the program runs off the Pi) ----
    def _sim_init(self):
        self._sim_heater = 0.0
        self._sim_stepper = 0.0
        self._sim_spooler_duty = 0.0
        self._sim_fan = 0.0
        self._sim_temp = 25.0
        # start mid-range so the (decrementing) count never wraps during a run;
        # the real encoder's sign convention makes a spooling motor read POSITIVE
        # rpm, so the sim decrements the position to match.
        self._sim_pos = float(2 ** 31)
        self._sim_last = time.perf_counter()
        # spooler calibration (from calibration.yaml): rpm = (duty - b)/m
        self._sim_slope, self._sim_intercept = load_motor_calibration()

    def _sim_advance(self):
        now = time.perf_counter()
        dt = now - self._sim_last
        self._sim_last = now
        # first-order thermal response toward an equilibrium set by heater duty
        import random
        equil = 25.0 + 2.1 * self._sim_heater      # ~230 C at 100 %
        self._sim_temp += (equil - self._sim_temp) * min(1.0, dt / 25.0)
        self._sim_temp += random.gauss(0, 0.15)
        # spooler: duty -> rpm via the inverse calibration, integrate position
        rpm = 0.0
        if self._sim_spooler_duty > 0 and self._sim_slope:
            rpm = (self._sim_spooler_duty - self._sim_intercept) / self._sim_slope
        rpm = max(0.0, min(70.0, rpm)) + random.gauss(0, 0.8)
        self._sim_pos -= rpm / 60.0 * self.PULSES_PER_REV * dt
        return dt

    def _sim_read_encoder(self):
        self._sim_advance()
        return int(self._sim_pos) & 0xFFFFFFFF

    def _sim_read_voltage(self):
        # invert Steinhart-Hart to fake a plausible ADC voltage for _sim_temp
        t_k = self._sim_temp + 273.15
        try:
            r = self.R_REF * math.exp(self.BETA * (1.0 / t_k - 1.0 / self.REF_T))
            v = self.VSUP * self.RESISTOR / (r + self.RESISTOR)
            return max(0.0001, min(self.VSUP - 1e-4, v))
        except (ValueError, OverflowError):
            return 0.5


def load_motor_calibration():
    """Read motor_slope / motor_intercept from calibration.yaml (fallback: file
    values we saw in the repo)."""
    slope, intercept = 2.5377984045767166, -51.304312538704494
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.yaml")
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.unsafe_load(fh)
        slope = float(data.get("motor_slope", slope))
        intercept = float(data.get("motor_intercept", intercept))
    except Exception:
        pass
    return slope, intercept


# =========================================================================== #
# Shared sensor state: written by the sampler, read by control + logging.
# =========================================================================== #
class SensorState:
    def __init__(self):
        self.lock = threading.Lock()
        self.enc_count = 0
        self.enc_time = 0.0
        self.voltage = 0.0
        self.temp_raw = 0.0
        self.temp_filt = 0.0
        self.rpm_raw = 0.0
        self.rpm_filt = 0.0
        self.diameter_raw = 0.0     # from the laptop stream
        self.diameter_filt = 0.0
        self.diameter_found = False
        self.diameter_units = "mm"
        self.last_diameter_time = 0.0


# =========================================================================== #
# Experiment state machine (headless port of experiment.py)
# =========================================================================== #
class Experiment:
    IDLE, HEATING, EXTRUDING, SETTLE, RECORDING, SPOOLING, COMPLETE, ABORTED = (
        "idle", "heating", "extruding", "settle", "recording", "spooling",
        "complete", "aborted")

    COLUMNS = ["Time (s)", "Temperature (C)", "Temperature raw (C)",
               "Temp setpoint (C)", "Temp error (C)", "Temp PID output",
               "Diameter (mm)", "Diameter raw (mm)", "Diameter setpoint (mm)",
               "Fan duty (%)", "Extruder RPM", "Spooler setpoint (RPM)",
               "Spooler RPM", "Spooler RPM raw"]

    def __init__(self):
        self.lock = threading.Lock()
        self.active = False
        self.abort_pending = False
        self.phase = self.IDLE
        self.params = {}
        self.phase_start = None
        self.t0 = 0.0
        self.remaining = 0.0
        self.rows = []
        self.csv_result = None
        self.csv_name = "fred_experiment"
        self.message = "idle"

    def start(self, params):
        with self.lock:
            self.params = dict(params or {})
            self.csv_name = str(self.params.get("name", "fred_experiment"))
            self.rows = []
            self.csv_result = None
            self.phase_start = None
            self.abort_pending = False
            self.phase = self.HEATING
            self.active = True
            self.message = "experiment received - heating"

    def request_abort(self):
        with self.lock:
            self.abort_pending = True

    def delay(self, key, default=0.0):
        try:
            return float(self.params.get(key, default))
        except (TypeError, ValueError):
            return default

    def mode(self, key, default="closed"):
        return str(self.params.get(key, default))

    def num(self, key, default=0.0):
        try:
            return float(self.params.get(key, default))
        except (TypeError, ValueError):
            return default


# =========================================================================== #
# The controller: threads for sampling, control, network, and terminal.
# =========================================================================== #
class FredTerminal:
    def __init__(self, hw, host="0.0.0.0", port=STREAM_PORT, csv_dir=".",
                 no_color=False, override_camera=False):
        self.hw = hw
        self.host = host
        self.port = port
        self.csv_dir = csv_dir
        self.no_color = no_color
        self.override_camera = override_camera
        self._pending_params = None       # experiment held until the camera is live
        self.sensors = SensorState()
        self.exp = Experiment()
        self._stop = threading.Event()

        # EMA filters for the fast (50 Hz) logging path -- presets from signal_filter
        self.f_temp = EMAFilter(ALPHA_TEMPERATURE)
        self.f_rpm = EMAFilter(ALPHA_SPOOLER_RPM)
        self.f_dia = EMAFilter(ALPHA_DIAMETER)

        # control-loop state
        self._c_prev_count = None
        self._c_prev_time = None
        self._heat_int = 0.0
        self._heat_prev_err = 0.0
        self._spool_int = 0.0
        self._spool_prev_err = 0.0
        self.slope, self.intercept = load_motor_calibration()

        # networking
        self._client = None
        self._send_lock = threading.Lock()

        # sample-rate read-out
        self._sample_count = 0
        self._rate_t0 = time.perf_counter()
        self._eff_rate = 0.0

    # ------------------------------------------------------------------ #
    # Sampler: the only SPI reader. 50 Hz. Filters + (during RECORDING) logs.
    # ------------------------------------------------------------------ #
    def sampler_loop(self):
        t0 = time.perf_counter()
        next_t = t0
        prev_count = self.hw.read_encoder()
        prev_time = time.perf_counter()
        while not self._stop.is_set():
            now = time.perf_counter()
            count = self.hw.read_encoder()
            voltage = self.hw.read_voltage()

            dt = now - prev_time
            rpm_raw = 0.0
            if dt > 0:
                # signed 32-bit delta, same sign convention as spooler.py
                delta = (count - prev_count)
                rpm_raw = -(delta / Hardware.PULSES_PER_REV) * (60.0 / dt)
                if abs(rpm_raw) > 70:      # reject encoder wrap / glitches
                    rpm_raw = self.sensors.rpm_raw
            prev_count, prev_time = count, now

            temp_raw = Hardware.voltage_to_temp(voltage)

            temp_filt = self.f_temp.update(temp_raw)
            rpm_filt = self.f_rpm.update(rpm_raw)

            if self.hw.sim:   # fake a live camera stream so the demo passes the interlock
                with self.sensors.lock:
                    self.sensors.diameter_raw = 0.35 + random.gauss(0, 0.01)
                    self.sensors.diameter_found = True
                    self.sensors.last_diameter_time = now

            with self.sensors.lock:
                self.sensors.enc_count = count
                self.sensors.enc_time = now
                self.sensors.voltage = voltage
                self.sensors.temp_raw = temp_raw
                self.sensors.temp_filt = temp_filt
                self.sensors.rpm_raw = rpm_raw
                self.sensors.rpm_filt = rpm_filt
                dia_raw = self.sensors.diameter_raw
                dia_found = self.sensors.diameter_found
            # diameter filter advances only on a detected measurement
            if dia_found:
                dia_filt = self.f_dia.update(dia_raw)
            else:
                dia_filt = self.f_dia.value
            with self.sensors.lock:
                self.sensors.diameter_filt = dia_filt

            # log a wide row while recording
            with self.exp.lock:
                recording = self.exp.active and self.exp.phase == Experiment.RECORDING
                t_rel = now - self.exp.t0 if recording else 0.0
            if recording:
                self._log_row(t_rel, temp_raw, temp_filt, dia_raw, dia_filt)

            # effective-rate read-out
            self._sample_count += 1
            if now - self._rate_t0 >= 1.0:
                self._eff_rate = self._sample_count / (now - self._rate_t0)
                self._sample_count = 0
                self._rate_t0 = now

            next_t += SAMPLE_DT
            sleep = next_t - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.perf_counter()   # fell behind: resync, do not spiral

    def _log_row(self, t_rel, temp_raw, temp_filt, dia_raw, dia_filt):
        with self.sensors.lock:
            rpm_raw = self.sensors.rpm_raw
            rpm_filt = self.sensors.rpm_filt
        p = self.exp.params
        heat_sp = self.exp.num("target_temperature")
        dia_sp = self.exp.num("target_diameter", 0.35)
        fan = self.exp.num("fan_duty")
        ext = self.exp.num("extrusion_speed")
        spool_sp = self.exp.num("motor_setpoint")
        row = [t_rel, temp_filt, temp_raw, heat_sp, heat_sp - temp_filt, "",
               dia_filt, dia_raw, dia_sp, fan, ext, spool_sp, rpm_filt, rpm_raw]
        with self.exp.lock:
            self.exp.rows.append(row)

    # ------------------------------------------------------------------ #
    # Control: 10 Hz PID for heater + spooler. Consumes cached sensor data.
    # ------------------------------------------------------------------ #
    def control_loop(self):
        next_t = time.perf_counter()
        while not self._stop.is_set():
            self._check_pending_start()
            self._service_experiment()
            next_t += CONTROL_DT
            sleep = next_t - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.perf_counter()

    def _service_experiment(self):
        exp = self.exp
        with exp.lock:
            if exp.abort_pending and exp.active:
                exp.abort_pending = False
                exp.active = False
                exp.phase = Experiment.ABORTED
                exp.remaining = 0.0
                exp.message = "aborted - all systems stopped"
                self.hw.all_off()
                self._reset_pid()
                self._notify()
                return
            if not exp.active:
                return
            t = time.perf_counter()
            if exp.phase_start is None:
                exp.phase_start = t
            phase = exp.phase
            elapsed = t - exp.phase_start

        if phase == Experiment.HEATING:
            self._drive_heater()
            self.hw.set_stepper_rpm(0); self.hw.set_spooler_duty(0); self.hw.set_fan(0)
            self._advance(elapsed, exp.delay("heating_delay"), Experiment.EXTRUDING,
                          "heating done - extruding")
        elif phase == Experiment.EXTRUDING:
            self._drive_heater()
            self.hw.set_stepper_rpm(exp.num("heat_extrude_speed"))
            self.hw.set_spooler_duty(0); self.hw.set_fan(0)
            self._advance(elapsed, exp.delay("heat_extrude_time"), Experiment.SETTLE,
                          "primed - all systems on")
        elif phase == Experiment.SETTLE:
            self._drive_all()
            self._advance(elapsed, exp.delay("data_delay"), Experiment.RECORDING,
                          "recording started", on_record=True)
        elif phase == Experiment.RECORDING:
            self._drive_all()
            if self._advance(elapsed, exp.delay("data_taking_time"), None, ""):
                self._build_csv()
                spool_time = exp.delay("post_spool_time")
                if spool_time > 0:
                    self.hw.set_heater(0); self.hw.set_stepper_rpm(0); self.hw.set_fan(0)
                    with exp.lock:
                        exp.phase = Experiment.SPOOLING
                        exp.phase_start = time.perf_counter()
                        exp.message = f"recording done - spooling {spool_time:.0f}s (data ready)"
                    self._notify()
                else:
                    self._finish()
        elif phase == Experiment.SPOOLING:
            self._drive_spooler()
            if self._advance(elapsed, exp.delay("post_spool_time"), None, ""):
                self.hw.set_spooler_duty(0)
                self._finish()

    def _advance(self, elapsed, duration, next_phase, message, on_record=False):
        """Update remaining time; if the phase is over, move on. Returns True if
        the phase just completed (used by RECORDING/SPOOLING to run custom code)."""
        with self.exp.lock:
            self.exp.remaining = max(0.0, duration - elapsed)
        if elapsed < duration:
            return False
        if next_phase is not None:
            with self.exp.lock:
                self.exp.phase = next_phase
                self.exp.phase_start = time.perf_counter()
                self.exp.message = message
                if on_record:
                    self.exp.t0 = time.perf_counter()
                    self.exp.rows = []
                    self.f_temp.reset(); self.f_rpm.reset(); self.f_dia.reset()
            self._notify()
        return True

    def _finish(self):
        self.hw.all_off()
        with self.exp.lock:
            self.exp.active = False
            self.exp.phase = Experiment.COMPLETE
            self.exp.remaining = 0.0
            self.exp.message = "complete - data ready to retrieve"
        self._notify()

    # ---- actuator drivers (PID identical in spirit to extruder/spooler) ----
    def _drive_heater(self):
        exp = self.exp
        with self.sensors.lock:
            temp = self.sensors.temp_filt
            temp_raw = self.sensors.temp_raw
        # safety first
        if temp >= TEMP_MAX or temp_raw < TEMP_SENSOR_MIN:
            self.hw.set_heater(0)
            self._heat_int = 0.0
            return
        if exp.mode("heater_mode") == "open":
            self.hw.set_heater(exp.num("heater_pwm"))
            return
        sp = exp.num("target_temperature")
        kp = exp.num("temp_kp", 1.0); ki = exp.num("temp_ki", 0.001); kd = exp.num("temp_kd", 0.05)
        err = sp - temp
        self._heat_int += err * CONTROL_DT
        deriv = (err - self._heat_prev_err) / CONTROL_DT
        self._heat_prev_err = err
        out = max(0.0, min(100.0, kp * err + ki * self._heat_int + kd * deriv))
        self.hw.set_heater(out)

    def _drive_spooler(self):
        exp = self.exp
        if exp.mode("spooler_mode") == "open":
            self.hw.set_spooler_duty(exp.num("dc_motor_pwm"))
            return
        with self.sensors.lock:
            count = self.sensors.enc_count
            ctime = self.sensors.enc_time
        rpm = 0.0
        if self._c_prev_count is not None:
            dt = ctime - self._c_prev_time
            if dt > 0:
                rpm = -((count - self._c_prev_count) / Hardware.PULSES_PER_REV) * (60.0 / dt)
                if abs(rpm) > 70:
                    rpm = 0.0
        self._c_prev_count, self._c_prev_time = count, ctime
        sp = exp.num("motor_setpoint")
        kp = exp.num("motor_kp", 0.5); ki = exp.num("motor_ki", 0.5); kd = exp.num("motor_kd", 0.05)
        err = sp - rpm
        self._spool_int = max(-100.0, min(100.0, self._spool_int + err * CONTROL_DT))
        deriv = (err - self._spool_prev_err) / CONTROL_DT
        self._spool_prev_err = err
        out = kp * err + ki * self._spool_int + kd * deriv
        duty = max(0.0, min(100.0, self.slope * out + self.intercept))
        self.hw.set_spooler_duty(duty)

    def _drive_all(self):
        self._drive_heater()
        self._drive_spooler()
        self.hw.set_stepper_rpm(self.exp.num("extrusion_speed"))
        self.hw.set_fan(self.exp.num("fan_duty"))

    def _reset_pid(self):
        self._heat_int = self._heat_prev_err = 0.0
        self._spool_int = self._spool_prev_err = 0.0
        self._c_prev_count = self._c_prev_time = None

    # ------------------------------------------------------------------ #
    # CSV build (semicolon + comma decimals, like experiment.py)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _fmt(v):
        if v == "" or v is None:
            return ""
        try:
            return f"{float(v):.4f}".replace(".", ",")
        except (TypeError, ValueError):
            return str(v)

    def _build_csv(self):
        with self.exp.lock:
            rows = list(self.exp.rows)
            cols = Experiment.COLUMNS
        lines = [";".join(cols)]
        for row in rows:
            lines.append(";".join(self._fmt(v) for v in row))
        text = "\r\n".join(lines) + "\r\n"
        with self.exp.lock:
            self.exp.csv_result = text
        # also save a local copy on the Pi
        try:
            path = os.path.join(self.csv_dir, self.exp.csv_name + ".csv")
            with open(path, "w", newline="", encoding="utf-8") as fh:
                fh.write(text)
            print(f"\n[saved] {path}  ({len(rows)} rows)")
        except OSError as exc:
            print(f"\n[warn] could not save local CSV: {exc}")

    # ------------------------------------------------------------------ #
    # Networking: TCP server, same protocol as external_diameter.py
    # ------------------------------------------------------------------ #
    def network_loop(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.settimeout(1.0)
        srv.bind((self.host, self.port))
        srv.listen(1)
        print(f"[net] listening on {self.host}:{self.port} "
              f"(SSID FrED_Pi / 192.168.4.1)")
        buf = b""
        while not self._stop.is_set():
            try:
                client, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            print(f"[net] laptop connected from {addr[0]}:{addr[1]}")
            client.settimeout(READ_TIMEOUT)
            self._client = client
            buf = b""
            while not self._stop.is_set():
                try:
                    data = client.recv(1024)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._handle_line(line)
            self._client = None
            try:
                client.close()
            except OSError:
                pass
            print("[net] laptop disconnected; waiting for a new connection...")
        srv.close()

    def _handle_line(self, raw):
        try:
            msg = json.loads(raw.decode("utf-8", "ignore").strip() or "{}")
        except (ValueError, json.JSONDecodeError):
            return
        mtype = msg.get("type")
        if mtype == "experiment":
            self._try_start_experiment(msg.get("params", {}))
        elif mtype == "abort":
            self.exp.request_abort()
            print("[exp] abort requested by laptop")
        elif mtype == "get_data":
            self._send_data()
        elif "d" in msg:            # diameter stream sample
            self._ingest_diameter(msg)

    def _ingest_diameter(self, msg):
        try:
            d = float(msg.get("d", 0.0))
        except (TypeError, ValueError):
            return
        found = bool(msg.get("found", True))
        with self.sensors.lock:
            self.sensors.diameter_raw = d
            self.sensors.diameter_found = found
            self.sensors.diameter_units = msg.get("u", "mm")
            self.sensors.last_diameter_time = time.perf_counter()

    # ------------------------------------------------------------------ #
    # Camera interlock: no experiment starts until the camera stream is live,
    # unless overridden (launch flag, 'o' on the terminal, or an override in
    # the experiment params).
    # ------------------------------------------------------------------ #
    def _camera_present(self):
        with self.sensors.lock:
            last = self.sensors.last_diameter_time
        return bool(last) and (time.perf_counter() - last) < CAMERA_FRESH_S

    def _try_start_experiment(self, params):
        override = self.override_camera or bool(params.get("override_camera"))
        if self._camera_present() or override:
            no_cam = override and not self._camera_present()
            self.exp.start(params)
            tag = "  (camera lock OVERRIDDEN)" if no_cam else ""
            self._c(f"\nStarting experiment '{self.exp.csv_name}'{tag}", GREEN, bold=True)
            self._notify()
        else:
            self._pending_params = params
            self._c("\nExperiment received, but NO camera signal detected.",
                    YELLOW, bold=True)
            self._c("Holding until the camera stream arrives. Type 'o' + Enter to "
                    "override (or relaunch with --override-camera).", YELLOW)
            self._send_status_msg("waiting for camera signal")

    def _check_pending_start(self):
        """Called from the control loop: start a held experiment once the
        camera is live (or an override arrives)."""
        if self._pending_params is None:
            return
        if self._camera_present() or self.override_camera:
            params, self._pending_params = self._pending_params, None
            self.exp.start(params)
            self._c("\nCamera signal OK - starting experiment", GREEN, bold=True)
            self._notify()

    def _send_status_msg(self, message):
        self._send({"type": "status", "phase": self.exp.phase, "remaining": 0.0,
                    "message": message, "data_ready": bool(self.exp.csv_result)})

    def _send(self, obj):
        client = self._client
        if client is None:
            return False
        with self._send_lock:
            try:
                client.settimeout(SEND_TIMEOUT)
                client.sendall((json.dumps(obj) + "\n").encode("utf-8"))
                client.settimeout(READ_TIMEOUT)
                return True
            except OSError:
                try:
                    client.close()
                except OSError:
                    pass
                self._client = None
                return False

    def _notify(self):
        with self.exp.lock:
            payload = {
                "type": "status", "phase": self.exp.phase,
                "remaining": round(self.exp.remaining, 1),
                "message": self.exp.message,
                "data_ready": bool(self.exp.csv_result),
            }
        self._send(payload)

    def _send_data(self):
        with self.exp.lock:
            csv_text = self.exp.csv_result
            name = self.exp.csv_name
        if not csv_text:
            self._send({"type": "event", "event": "no_data",
                        "message": "No experiment data available yet."})
            return
        b64 = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
        self._send({"type": "data", "format": "csv", "name": name, "b64": b64})
        print("[net] recorded data sent to laptop")

    # ------------------------------------------------------------------ #
    # Terminal announcer: replaces the GUI. Colored phase headers, per-phase
    # countdowns, and the live filtered readings during recording -- printed
    # at the cadence you asked for, without flooding the terminal.
    # ------------------------------------------------------------------ #
    def _c(self, text, color, bold=False):
        """Print one colored line (plain if --no-color)."""
        if self.no_color:
            print(text, flush=True)
        else:
            print(f"{color}{BOLD if bold else ''}{text}{RESET}", flush=True)

    # phase -> (color, English header)
    HEADERS = {
        Experiment.HEATING:   (CYAN,    "HEATING (heater only)"),
        Experiment.EXTRUDING: (MAGENTA, "HEATING + EXTRUSION"),
        Experiment.SETTLE:    (BLUE,    "SETTLING (all systems on)"),
        Experiment.RECORDING: (GREEN,   "RECORDING - data acquisition"),
        Experiment.SPOOLING:  (YELLOW,  "EXTRA SPOOLING (data ready)"),
        Experiment.COMPLETE:  (GREEN,   "EXPERIMENT COMPLETE - data ready to retrieve"),
        Experiment.ABORTED:   (RED,     "ABORTED - all systems stopped"),
    }

    def announcer_loop(self):
        last_phase = None
        printed = set()
        while not self._stop.is_set():
            with self.exp.lock:
                phase = self.exp.phase
                remaining = self.exp.remaining
                total_rec = self.exp.num("data_taking_time", 0.0)

            if phase != last_phase:
                printed = set()
                last_phase = phase
                if phase in self.HEADERS:
                    color, text = self.HEADERS[phase]
                    self._c(f"\n=== {text} ===", color, bold=True)
                if phase == Experiment.RECORDING:
                    self._c(">>> Experiment initialized <<<", GREEN, bold=True)
                    self._c("    time    Temp(C)   Spooler(RPM)   Diameter(mm)   [filtered]",
                            GREY)

            if phase == Experiment.HEATING:
                # every 5 s, then a 5-4-3-2-1 tail in one color (cyan)
                self._countdown(remaining, printed, CYAN, step=5, tail=5, tail_colors=None)
            elif phase == Experiment.EXTRUDING:
                # every 5 s, then 3 (green) 2 (yellow) 1 (red)
                self._countdown(remaining, printed, MAGENTA, step=5, tail=3,
                                tail_colors={3: GREEN, 2: YELLOW, 1: RED})
            elif phase == Experiment.SETTLE:
                self._each_second(remaining, printed, BLUE, "Settling")
            elif phase == Experiment.RECORDING:
                self._record_second(remaining, total_rec, printed)
            elif phase == Experiment.SPOOLING:
                self._each_second(remaining, printed, YELLOW, "Spooling")
            time.sleep(0.12)

    def _countdown(self, remaining, printed, color, step, tail, tail_colors):
        r = max(0.0, remaining)
        if r > tail:
            m = int(r)                       # coarse phase: print at each `step` boundary
            if m > tail and m % step == 0 and ("s", m) not in printed:
                printed.add(("s", m))
                self._c(f"   {m} s remaining...", color)
        else:
            sec = int(math.ceil(r))          # tail: one number per second
            if 1 <= sec <= tail and ("t", sec) not in printed:
                printed.add(("t", sec))
                col = (tail_colors or {}).get(sec, color)
                self._c(f"      {sec}", col, bold=True)

    def _each_second(self, remaining, printed, color, label):
        sec = int(math.ceil(max(0.0, remaining)))
        if sec >= 1 and ("s", sec) not in printed:
            printed.add(("s", sec))
            self._c(f"   {label}: {sec} s remaining", color)

    def _record_second(self, remaining, total, printed):
        # skip the first tick before the control loop has set `remaining`
        # (avoids a spurious "t=<total>" line at the very start of recording)
        if remaining <= 0.001:
            return
        sec = int(math.ceil(max(0.0, remaining)))
        if ("r", sec) in printed:
            return
        printed.add(("r", sec))
        with self.sensors.lock:
            t = self.sensors.temp_filt
            rpm = self.sensors.rpm_filt
            dia = self.sensors.diameter_filt
            age = (time.perf_counter() - self.sensors.last_diameter_time
                   if self.sensors.last_diameter_time else 999)
        elapsed = max(0.0, total - remaining)
        dia_txt = f"{dia:8.3f}" if age < 2 else "   n/a  "
        self._c(f"   t={elapsed:6.1f}s  {t:7.2f}   {rpm:9.1f}     {dia_txt}", WHITE)

    # ------------------------------------------------------------------ #
    # Operator commands typed on the terminal (a minimal control surface):
    #   o / override -> lift the camera interlock
    #   a / abort    -> abort the running experiment
    #   q / quit     -> shut down
    # ------------------------------------------------------------------ #
    def stdin_loop(self):
        try:
            for line in sys.stdin:
                cmd = line.strip().lower()
                if cmd in ("o", "override"):
                    self.override_camera = True
                    self._c("[operator] camera lock overridden", MAGENTA, bold=True)
                elif cmd in ("a", "abort"):
                    self.exp.request_abort()
                    self._c("[operator] abort requested", RED, bold=True)
                elif cmd in ("q", "quit"):
                    self._stop.set()
                    break
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def run(self, demo=False, experiment_file=None):
        threads = [
            threading.Thread(target=self.sampler_loop, daemon=True),
            threading.Thread(target=self.control_loop, daemon=True),
            threading.Thread(target=self.announcer_loop, daemon=True),
            threading.Thread(target=self.stdin_loop, daemon=True),
        ]
        if not demo:
            threads.append(threading.Thread(target=self.network_loop, daemon=True))
        for th in threads:
            th.start()
        if experiment_file:
            self._start_from_file(experiment_file)
        elif demo:
            self._run_demo()
        try:
            while not self._stop.is_set():
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\n[exit] stopping...")
        finally:
            self._stop.set()
            time.sleep(0.3)
            self.hw.cleanup()
            print("[exit] hardware off. done.")

    def _run_demo(self):
        """Kick off a short local experiment so you can watch it end-to-end."""
        self._c("[demo] built-in experiment (no laptop needed)", GREY)
        self._try_start_experiment({
            "name": "demo_experiment", "heater_mode": "closed",
            "target_temperature": 90, "temp_kp": 1.0, "temp_ki": 0.001, "temp_kd": 0.05,
            "spooler_mode": "closed", "motor_setpoint": 30, "motor_kp": 0.5,
            "motor_ki": 0.5, "motor_kd": 0.05, "extrusion_speed": 1.5,
            "fan_duty": 40, "target_diameter": 0.35,
            "heating_delay": 5, "heat_extrude_time": 3, "heat_extrude_speed": 1.5,
            "data_delay": 2, "data_taking_time": 10, "post_spool_time": 3,
        })

    def _start_from_file(self, path):
        """Run an experiment whose sequence you wrote in a local JSON file."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                params = json.load(fh)
        except (OSError, ValueError) as exc:
            self._c(f"Could not read experiment file '{path}': {exc}", RED, bold=True)
            return
        self._c(f"Loaded experiment sequence from {path}", CYAN)
        self._try_start_experiment(params)


def main():
    ap = argparse.ArgumentParser(description="Headless terminal FrED experiment runner.")
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default: all)")
    ap.add_argument("--port", type=int, default=STREAM_PORT)
    ap.add_argument("--sim", action="store_true", help="simulate hardware (run off the Pi)")
    ap.add_argument("--demo", action="store_true", help="run a built-in experiment, no laptop")
    ap.add_argument("--experiment", metavar="FILE.json",
                    help="run an experiment sequence from a local JSON file (no laptop)")
    ap.add_argument("--override-camera", action="store_true",
                    help="start experiments without waiting for the camera signal")
    ap.add_argument("--no-color", action="store_true", help="disable colored output")
    ap.add_argument("--csv-dir", default=".", help="where to save the recorded CSV")
    args = ap.parse_args()

    if not args.no_color:
        enable_ansi()
    print("=" * 64)
    print(" FrED terminal runner  |  sample 50 Hz  control 10 Hz  |  "
          + ("SIM" if args.sim else "HARDWARE"))
    print(" commands: 'o'=override camera lock  'a'=abort  'q'=quit")
    print("=" * 64)
    hw = Hardware(sim=args.sim)
    app = FredTerminal(hw, host=args.host, port=args.port, csv_dir=args.csv_dir,
                       no_color=args.no_color, override_camera=args.override_camera)
    app.run(demo=args.demo, experiment_file=args.experiment)


if __name__ == "__main__":
    main()
