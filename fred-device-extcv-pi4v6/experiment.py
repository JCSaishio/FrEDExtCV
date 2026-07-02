"""Remote experiment controller for FrED (v6).

An experiment is configured on the laptop (the CV app) and sent to the Pi over
the existing WiFi/TCP link. This controller runs the automated sequence:

    HEATING   - heater only, for ``heating_delay`` seconds
    EXTRUDING - heater + extrusion stepper (at its own independent rate,
                ``heat_extrude_speed`` RPM), for ``heat_extrude_time`` seconds
    SETTLE    - all systems activated, wait ``data_delay`` seconds
    RECORDING - everything running AND data recorded, for ``data_taking_time``
    SPOOLING  - everything stopped EXCEPT the spooler, which keeps running for
                ``post_spool_time`` seconds to coil the already-extruded fiber
    COMPLETE  - all actuators stopped, recorded CSV held until the laptop asks
                for it (the CSV is already available during SPOOLING)

All data is timestamped on the Pi's own clock and, for the export, rebased so
the recording starts at t = 0. The heater and spooler can each run closed-loop
(setpoint + PID) or open-loop (raw PWM), chosen per experiment.

The Pi's graphs are reset twice: when the experiment is received (clean run
view) and again the moment RECORDING starts, so the on-screen plots show
exactly the window that is exported to the CSV/Excel.

An abort - the laptop's Abort button or a red STOP button on the Pi - stops
EVERY system (heater, stepper, spooler, fan) and clears the manual control
flags, so nothing keeps running or resumes on its own afterwards.

The controller is driven once per hardware-loop iteration by
:meth:`update`, and reads/writes only plain Python attributes, so it is safe to
poke from the network thread (start/abort) while the hardware thread runs it.
"""
import base64
import threading

from database import Database


class Experiment:
    IDLE = "idle"
    HEATING = "heating"
    EXTRUDING = "extruding"      # heating + extrusion, before anything spools
    SETTLE = "settle"
    RECORDING = "recording"
    SPOOLING = "spooling"        # post-run: spooler only, coiling loose fiber
    COMPLETE = "complete"
    ABORTED = "aborted"

    # One wide table: a single time column, then every measurement to the right.
    COLUMNS = ["Time (s)", "Temperature (C)", "Temp setpoint (C)",
               "Temp error (C)", "Temp PID output", "Temp Kp", "Temp Ki",
               "Temp Kd", "Diameter (mm)", "Diameter setpoint (mm)",
               "Fan duty (%)", "Extruder RPM", "Spooler setpoint (RPM)",
               "Spooler RPM", "Spooler Kp", "Spooler Ki", "Spooler Kd"]

    def __init__(self, gui) -> None:
        self.gui = gui
        self.active = False
        self.abort_pending = False   # set by abort(); serviced in update()
        self.phase = Experiment.IDLE
        self.params = {}
        self.phase_start = None      # set on the first update() (Pi clock)
        self.t0 = 0.0                # recording start (export clock origin)
        self.remaining = 0.0
        self._rows = []              # one logged row per sample (wide table)
        self._last_aux = 0.0         # cadence gate for stepper/diameter/fan
        self._last_log = 0.0         # cadence gate for logging a data row
        self.csv_result = None       # built CSV text, ready to send
        self.csv_name = "fred_experiment"

    # ------------------------------------------------------------------ #
    # Commands (called from the network thread)
    # ------------------------------------------------------------------ #
    def start(self, params: dict) -> None:
        self.params = dict(params or {})
        self.csv_name = str(self.params.get("name", "fred_experiment"))
        self.csv_result = None
        self._rows = []
        self.phase_start = None
        self._last_aux = 0.0
        self._last_log = 0.0
        self.abort_pending = False
        self.phase = Experiment.HEATING
        self.active = True
        # Ask the Pi UI to reset its graphs for a clean view of this run.
        try:
            self.gui.pending_graph_reset = True
        except Exception:
            pass
        self._notify(Experiment.HEATING, "Experiment received - heating")

    def abort(self) -> None:
        """Abort request (laptop Abort button or a red STOP on the Pi).

        Called from the network thread, so it only sets flags - the actual
        actuator shutdown runs in the hardware thread (see update() ->
        _do_abort), which owns the hardware objects. If no run is active the
        laptop's Abort still acts as a remote ALL-STOP: the main loop's stop
        handlers zero every output.
        """
        if self.active:
            self.abort_pending = True
            return
        # No run to unwind - remote all-stop via the one-shot stop flags that
        # the hardware loop already services (they actively zero the outputs).
        self.phase = Experiment.ABORTED
        self.remaining = 0.0
        self._all_systems_off_flags()
        self._notify(Experiment.ABORTED, "Abort received - all systems stopped")

    def _all_systems_off_flags(self) -> None:
        """Clear every manual-control flag and request every output to zero."""
        gui = self.gui
        gui.device_started = False
        gui.heater_open_loop_enabled = False
        gui.dc_motor_open_loop_enabled = False
        gui.dc_motor_close_loop_enabled = False
        gui.fan_enabled = False          # fan stays off until restarted in the UI
        gui.heater_stop_requested = True
        gui.stepper_stop_requested = True
        gui.dc_motor_stop_requested = True

    def _do_abort(self, extruder, spooler, fan) -> None:
        """Hardware-thread side of abort(): stop EVERY actuator, end the run."""
        self.abort_pending = False
        self.active = False
        self.phase = Experiment.ABORTED
        self.remaining = 0.0
        self._stop_all(extruder, spooler, fan)
        self._all_systems_off_flags()
        self._notify(Experiment.ABORTED,
                     "Experiment aborted - all systems stopped")

    def is_active(self) -> bool:
        return self.active

    # ------------------------------------------------------------------ #
    # Setpoint overrides (read by UserInterface.get_* accessors)
    # ------------------------------------------------------------------ #
    def override(self, key: str):
        """Return the experiment value for ``key`` while a run is active, else
        None so the GUI falls back to its manual widget value."""
        if not self.active:
            return None
        # During the heating+extrusion phase the stepper runs at its own,
        # independently configured rate (not the recording-phase rate).
        if (key == "extrusion_speed" and self.phase == Experiment.EXTRUDING
                and "heat_extrude_speed" in self.params):
            key = "heat_extrude_speed"
        if key not in self.params:
            return None
        try:
            return float(self.params[key])
        except (TypeError, ValueError):
            return None

    def _mode(self, key: str, default: str = "closed") -> str:
        return str(self.params.get(key, default))

    def _delay(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.params.get(key, default))
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------ #
    # Main state machine (called every hardware-loop iteration)
    # ------------------------------------------------------------------ #
    def update(self, t: float, extruder, spooler, fan) -> None:
        # An abort (laptop button or Pi STOP button) is serviced here, in the
        # hardware thread, so every actuator is actively driven to zero.
        if self.abort_pending and self.active:
            self._do_abort(extruder, spooler, fan)
            return
        if not self.active:
            return
        if self.phase_start is None:
            self.phase_start = t
            self._last_aux = t

        if self.phase == Experiment.HEATING:
            self._drive_heater(t, extruder)
            self._idle_movers(extruder, spooler, fan)
            self._tick_remaining(t, self._delay("heating_delay"))
            if t - self.phase_start >= self._delay("heating_delay"):
                self.phase = Experiment.EXTRUDING
                self.phase_start = t
                self._notify(Experiment.EXTRUDING,
                             "Heating done - extruding (heater + stepper)")

        elif self.phase == Experiment.EXTRUDING:
            # Heater + stepper only; the stepper speed comes from the
            # phase-specific ``heat_extrude_speed`` (see override()).
            self._drive_heater(t, extruder)
            period = self.gui.get_sample_period()
            if t - self._last_aux >= period:
                self._last_aux = t
                extruder.stepper_control_loop()
                self.gui.diameter_source.update(t)
            self._idle_spooler_fan(spooler, fan)
            self._tick_remaining(t, self._delay("heat_extrude_time"))
            if t - self.phase_start >= self._delay("heat_extrude_time"):
                self.phase = Experiment.SETTLE
                self.phase_start = t
                self._notify(Experiment.SETTLE,
                             "Extrusion primed - all systems activated")

        elif self.phase == Experiment.SETTLE:
            self._drive_all(t, extruder, spooler, fan)
            self._tick_remaining(t, self._delay("data_delay"))
            if t - self.phase_start >= self._delay("data_delay"):
                self.phase = Experiment.RECORDING
                self.phase_start = t
                self.t0 = t
                self._rows = []
                self._last_log = t
                # Clear the on-screen graphs right as recording begins, so the
                # plots show exactly the window that will be exported to the
                # CSV/Excel (handled on the GUI thread in _redraw_plots).
                self.gui.pending_graph_reset = True
                self._notify(Experiment.RECORDING, "Recording started")

        elif self.phase == Experiment.RECORDING:
            self._drive_all(t, extruder, spooler, fan)
            # Log one wide row at the user's data rate (independent of the fixed
            # control rate), so the CSV is dense without affecting control.
            if t - self._last_log >= self.gui.get_sample_period():
                self._last_log = t
                self._append_row(t)
            self._tick_remaining(t, self._delay("data_taking_time"))
            if t - self.phase_start >= self._delay("data_taking_time"):
                self._build_csv()
                spool_time = self._delay("post_spool_time")
                if spool_time > 0:
                    # Stop everything but the spooler, which keeps coiling the
                    # fiber already extruded. Data is ready to retrieve now.
                    self._stop_all_but_spooler(extruder, fan)
                    self.phase = Experiment.SPOOLING
                    self.phase_start = t
                    self._notify(Experiment.SPOOLING,
                                 f"Recording complete - spooling "
                                 f"{spool_time:.0f}s more (data ready)")
                else:
                    self._stop_all(extruder, spooler, fan)
                    self._finish(Experiment.COMPLETE,
                                 "Recording complete - data ready to retrieve")

        elif self.phase == Experiment.SPOOLING:
            # Only the spooler runs (same mode/setpoint as the experiment).
            if self._mode("spooler_mode") == "open":
                spooler.dc_motor_open_loop_control(t)
            else:
                spooler.dc_motor_close_loop_control(t)
            self._tick_remaining(t, self._delay("post_spool_time"))
            if t - self.phase_start >= self._delay("post_spool_time"):
                try:
                    spooler.stop_motor()
                except Exception as exc:
                    print(f"[Experiment] spooler stop error: {exc}")
                self._finish(Experiment.COMPLETE,
                             "Extra spooling done - data ready to retrieve")

    def _finish(self, phase: str, message: str) -> None:
        self.phase = phase
        self.active = False
        self.remaining = 0.0
        self._notify(phase, message)

    # ------------------------------------------------------------------ #
    # Actuator helpers
    # ------------------------------------------------------------------ #
    def _drive_heater(self, t: float, extruder) -> None:
        if self._mode("heater_mode") == "open":
            extruder.temperature_open_loop_control(t)
        else:
            extruder.temperature_control_loop(t)

    def _drive_all(self, t: float, extruder, spooler, fan) -> None:
        # Heater and spooler self-throttle to the sample period internally.
        self._drive_heater(t, extruder)
        if self._mode("spooler_mode") == "open":
            spooler.dc_motor_open_loop_control(t)
        else:
            spooler.dc_motor_close_loop_control(t)

        # Stepper, diameter and fan append every call, so gate them to the
        # sample period here to keep the logged rate aligned (~50 Hz default).
        period = self.gui.get_sample_period()
        if t - self._last_aux >= period:
            self._last_aux = t
            extruder.stepper_control_loop()
            self.gui.fan_enabled = True
            fan.control_loop()
            self.gui.diameter_source.update(t)

    def _idle_movers(self, extruder, spooler, fan) -> None:
        """Heating phase: heater on, everything that moves held at zero."""
        try:
            extruder.stop_stepper()
            spooler.stop_motor()
            fan.update_duty_cycle(0)
        except Exception as exc:
            print(f"[Experiment] idle error: {exc}")

    def _idle_spooler_fan(self, spooler, fan) -> None:
        """Heating+extrusion phase: spooler and fan held at zero."""
        try:
            spooler.stop_motor()
            fan.update_duty_cycle(0)
        except Exception as exc:
            print(f"[Experiment] idle error: {exc}")

    def _stop_all(self, extruder, spooler, fan) -> None:
        try:
            extruder.stop_heater()
            extruder.stop_stepper()
            spooler.stop_motor()
            fan.update_duty_cycle(0)
        except Exception as exc:
            print(f"[Experiment] stop error: {exc}")

    def _stop_all_but_spooler(self, extruder, fan) -> None:
        """End of recording: heater, stepper and fan off; spooler keeps going."""
        try:
            extruder.stop_heater()
            extruder.stop_stepper()
            fan.update_duty_cycle(0)
        except Exception as exc:
            print(f"[Experiment] stop error: {exc}")

    # ------------------------------------------------------------------ #
    # Recording window + CSV
    # ------------------------------------------------------------------ #
    def _append_row(self, t: float) -> None:
        """Snapshot the latest values into one wide row (single time column)."""
        def last(lst):
            return lst[-1] if lst else ""
        diameter = self.gui.diameter_source.get_latest()[0]
        self._rows.append([
            t - self.t0,
            last(Database.temperature_readings),
            last(Database.temperature_setpoint),
            last(Database.temperature_error),
            last(Database.temperature_pid_output),
            last(Database.temperature_kp),
            last(Database.temperature_ki),
            last(Database.temperature_kd),
            diameter,
            self.gui.get_target_diameter(),
            last(Database.fan_duty_cycle),
            last(Database.extruder_rpm),
            last(Database.spooler_setpoint),
            last(Database.spooler_rpm),
            last(Database.spooler_kp),
            last(Database.spooler_ki),
            last(Database.spooler_kd),
        ])

    @staticmethod
    def _num(value) -> str:
        """Format a value with a COMMA decimal separator (for Excel es-MX)."""
        if value == "" or value is None:
            return ""
        try:
            return f"{float(value):.4f}".replace(".", ",")
        except (TypeError, ValueError):
            return str(value)

    def _build_csv(self) -> None:
        """Build a single wide table: SEMICOLON-delimited, comma decimals."""
        try:
            lines = [";".join(self.COLUMNS)]
            for row in self._rows:
                lines.append(";".join(self._num(v) for v in row))
            self.csv_result = "\r\n".join(lines) + "\r\n"
        except Exception as exc:
            print(f"[Experiment] CSV build error: {exc}")
            self.csv_result = None

    def data_payload(self):
        """Return the {type:data,...} message for the laptop, or None."""
        if not self.csv_result:
            return None
        b64 = base64.b64encode(self.csv_result.encode("utf-8")).decode("ascii")
        return {"type": "data", "format": "csv", "name": self.csv_name, "b64": b64}

    # ------------------------------------------------------------------ #
    # Status / notifications
    # ------------------------------------------------------------------ #
    def _tick_remaining(self, t: float, duration: float) -> None:
        self.remaining = max(0.0, duration - (t - self.phase_start))

    PHASE_TEXT = {HEATING: "heating (heater only)",
                  EXTRUDING: "heating + extrusion",
                  SETTLE: "settling (all systems on)",
                  RECORDING: "recording",
                  SPOOLING: "extra spooling (data ready)"}

    def status_line(self) -> str:
        if self.phase == Experiment.IDLE:
            return "Experiment: idle"
        if self.phase == Experiment.COMPLETE:
            ready = " (data ready)" if self.csv_result else ""
            return f"Experiment: complete{ready}"
        if self.phase == Experiment.ABORTED:
            return "Experiment: aborted"
        text = self.PHASE_TEXT.get(self.phase, self.phase)
        return f"Experiment: {text} ({self.remaining:.0f}s left)"

    def _notify(self, phase: str, message: str) -> None:
        """Push a status update to the laptop (best-effort)."""
        try:
            self.gui.diameter_source.send_message({
                "type": "status",
                "phase": phase,
                "remaining": round(self.remaining, 1),
                "message": message,
                "data_ready": bool(self.csv_result),
            })
        except Exception:
            pass
