"""Remote experiment controller for FrED (v7).

An experiment is configured on the laptop (the CV app) and sent to the Pi over
the WiFi/TCP link (see ``laptop_link.py``). This controller runs the automated
sequence:

    HEATING   - heater only, for ``heating_delay`` seconds
    EXTRUDING - heater + extrusion stepper (at its own independent rate,
                ``heat_extrude_speed`` RPM), for ``heat_extrude_time`` seconds
    SETTLE    - all systems activated, wait ``data_delay`` seconds
    RECORDING - everything running AND data recorded, for ``data_taking_time``
    SPOOLING  - everything stopped EXCEPT the spooler, which keeps running for
                ``post_spool_time`` seconds to coil the already-extruded fiber
    COMPLETE  - all actuators stopped, recorded CSV held until the laptop asks
                for it (the CSV is already available during SPOOLING)

"Start recording now" (laptop button, v7): the operator presses it the moment
the fiber drops. During HEATING / EXTRUDING / SETTLE the run jumps straight to
RECORDING - every system on, t = 0 is that instant - and records for the full
``data_taking_time``. With no run active, the laptop's parameters start a new
run directly in RECORDING (the warm-up phases are skipped).

Sampling (v7): the temperature PID, the spooler PID and the logged data row all
run on ONE tick at the experiment's sample rate (``sample_rate_hz``, else the
Pi's Sampling-rate box). The row is written right after the control updates of
the same tick, so control and data share a single frequency and every row
carries fresh readings. "Temp new reading" / "Spooler new reading" (1 = a new
sensor reading in this row, 0 = value repeated from the previous row) document
that row by row.

Diameter (v7): no longer measured on the Pi. The laptop records it at the full
camera rate on its own clock and merges it into this table on Retrieve Data,
using the recording start ``t0`` reported here (Pi clock) and the clock sync
described in ``laptop_link.py``. The table therefore has no diameter columns.

The Pi's graphs are reset when the experiment is received (clean run view) and
again the moment RECORDING starts, so the on-screen plots show exactly the
window that is exported.

An abort - the laptop's Abort button or a red STOP button on the Pi - stops
EVERY system (heater, stepper, spooler, fan) and clears the manual control
flags, so nothing keeps running or resumes on its own afterwards.

The controller is driven once per hardware-loop iteration by :meth:`update`,
and reads/writes only plain Python attributes, so it is safe to poke from the
network thread (start/start_now/abort) while the hardware thread runs it.
"""
import base64

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

    WARM_UP = (HEATING, EXTRUDING, SETTLE)   # phases "start now" can skip

    # One wide table: a single time column, then every measurement to the
    # right. The laptop inserts its diameter columns just before "Diameter
    # setpoint (mm)" and a "Steady state" column at the end when it merges.
    COLUMNS = ["Time (s)", "Temperature (C)", "Temp setpoint (C)",
               "Temp error (C)", "Temp PID output", "Temp Kp", "Temp Ki",
               "Temp Kd", "Temp new reading", "Diameter setpoint (mm)",
               "Fan duty (%)", "Extruder RPM", "Spooler setpoint (RPM)",
               "Spooler RPM", "Spooler Kp", "Spooler Ki", "Spooler Kd",
               "Spooler new reading"]

    def __init__(self, gui) -> None:
        self.gui = gui
        self.active = False
        self.abort_pending = False   # set by abort(); serviced in update()
        self.record_now_pending = False   # set by start_now(); ditto
        self.phase = Experiment.IDLE
        self.params = {}
        self.phase_start = None      # set on the first update() (Pi clock)
        self.t0 = 0.0                # recording start (export clock origin)
        self.t_end = None            # recording end (Pi clock)
        self.remaining = 0.0
        self._rows = []              # one logged row per sample tick
        self._last_tick = None       # time of the last control/log tick
        self.csv_result = None       # built CSV text, ready to send
        self.csv_meta = {}           # timing info the laptop needs to merge
        self.csv_name = "fred_experiment"

    # ------------------------------------------------------------------ #
    # Commands (called from the network thread)
    # ------------------------------------------------------------------ #
    def start(self, params: dict) -> None:
        self.params = dict(params or {})
        self.csv_name = str(self.params.get("name", "fred_experiment"))
        self.csv_result = None
        self.csv_meta = {}
        self._rows = []
        self.phase_start = None
        self._last_tick = None
        self.t_end = None
        self.abort_pending = False
        start_now = bool(self.params.get("start_now"))
        # A direct start is serviced by update() like the button during a
        # run, so t0 is stamped on the hardware thread's clock reading.
        self.record_now_pending = start_now
        self.phase = Experiment.HEATING
        # The run owns the hardware from here on; clear the manual loops so
        # none of them silently resumes when the run ends.
        self._clear_manual_loops()
        self.active = True
        # Ask the Pi UI to reset its graphs for a clean view of this run.
        try:
            self.gui.pending_graph_reset = True
        except Exception:
            pass
        if start_now:
            self._notify(Experiment.HEATING,
                         "Experiment received - recording starts now")
        else:
            self._notify(Experiment.HEATING, "Experiment received - heating")

    def start_now(self, params: dict) -> None:
        """Laptop "Start recording now" button (fiber dropped).

        During the warm-up phases the run jumps straight to RECORDING (the
        switch itself happens in update(), on the hardware thread). With no
        run active the given parameters start a run directly in RECORDING.
        """
        if self.active:
            if self.phase in Experiment.WARM_UP:
                self.record_now_pending = True
            else:
                self._notify(self.phase, "Already past the warm-up - "
                                         "'start now' ignored")
            return
        params = dict(params or {})
        params["start_now"] = True
        self.start(params)

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

    def _clear_manual_loops(self) -> None:
        """Switch off every manual control loop (no outputs are touched)."""
        gui = self.gui
        gui.device_started = False
        gui.heater_open_loop_enabled = False
        gui.dc_motor_open_loop_enabled = False
        gui.dc_motor_close_loop_enabled = False

    def _all_systems_off_flags(self) -> None:
        """Clear every manual-control flag and request every output to zero."""
        gui = self.gui
        self._clear_manual_loops()
        gui.fan_enabled = False          # fan stays off until restarted in the UI
        gui.heater_stop_requested = True
        gui.stepper_stop_requested = True
        gui.dc_motor_stop_requested = True

    def _do_abort(self, extruder, spooler, fan) -> None:
        """Hardware-thread side of abort(): stop EVERY actuator, end the run."""
        self.abort_pending = False
        self.record_now_pending = False
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

        # "Start recording now": skip whatever is left of the warm-up.
        if self.record_now_pending:
            self.record_now_pending = False
            if self.phase in Experiment.WARM_UP:
                self._begin_recording(t, "Recording started NOW (operator)")

        # One tick drives every control loop AND the logged row, so control
        # and data run at the same frequency. The control loops gate on the
        # same period internally; comparing with the same ">" keeps them in
        # step with this tick.
        tick = (self._last_tick is None
                or t - self._last_tick > self.gui.get_sample_period())
        if tick:
            self._last_tick = t

        if self.phase == Experiment.HEATING:
            if tick:
                self._drive_heater(t, extruder)
                self._idle_movers(extruder, spooler, fan)
            self._tick_remaining(t, self._delay("heating_delay"))
            if t - self.phase_start >= self._delay("heating_delay"):
                self._enter(Experiment.EXTRUDING, t,
                            "Heating done - extruding (heater + stepper)")

        elif self.phase == Experiment.EXTRUDING:
            # Heater + stepper only; the stepper speed comes from the
            # phase-specific ``heat_extrude_speed`` (see override()).
            if tick:
                self._drive_heater(t, extruder)
                extruder.stepper_control_loop()
                self._idle_spooler_fan(spooler, fan)
            self._tick_remaining(t, self._delay("heat_extrude_time"))
            if t - self.phase_start >= self._delay("heat_extrude_time"):
                self._enter(Experiment.SETTLE, t,
                            "Extrusion primed - all systems activated")

        elif self.phase == Experiment.SETTLE:
            if tick:
                self._drive_all(t, extruder, spooler, fan)
            self._tick_remaining(t, self._delay("data_delay"))
            if t - self.phase_start >= self._delay("data_delay"):
                self._begin_recording(t, "Recording started")

        elif self.phase == Experiment.RECORDING:
            if tick:
                n_temp = len(Database.temperature_timestamps)
                n_spool = len(Database.spooler_timestamps)
                self._drive_all(t, extruder, spooler, fan)
                self._append_row(
                    t,
                    len(Database.temperature_timestamps) > n_temp,
                    len(Database.spooler_timestamps) > n_spool)
            self._tick_remaining(t, self._delay("data_taking_time"))
            if t - self.phase_start >= self._delay("data_taking_time"):
                self.t_end = t
                self._build_csv()
                spool_time = self._delay("post_spool_time")
                if spool_time > 0:
                    # Stop everything but the spooler, which keeps coiling the
                    # fiber already extruded. Data is ready to retrieve now.
                    self._stop_all_but_spooler(extruder, fan)
                    self._enter(Experiment.SPOOLING, t,
                                f"Recording complete - spooling "
                                f"{spool_time:.0f}s more (data ready)")
                else:
                    self._stop_all(extruder, spooler, fan)
                    self._finish(Experiment.COMPLETE,
                                 "Recording complete - data ready to retrieve")

        elif self.phase == Experiment.SPOOLING:
            # Only the spooler runs (same mode/setpoint as the experiment).
            if tick:
                self._drive_spooler(t, spooler)
            self._tick_remaining(t, self._delay("post_spool_time"))
            if t - self.phase_start >= self._delay("post_spool_time"):
                try:
                    spooler.stop_motor()
                except Exception as exc:
                    print(f"[Experiment] spooler stop error: {exc}")
                self._finish(Experiment.COMPLETE,
                             "Extra spooling done - data ready to retrieve")

    def _enter(self, phase: str, t: float, message: str) -> None:
        self.phase = phase
        self.phase_start = t
        self._notify(phase, message)

    def _begin_recording(self, t: float, message: str) -> None:
        """Switch to RECORDING at Pi time ``t`` (the export's t = 0)."""
        self.phase = Experiment.RECORDING
        self.phase_start = t
        self.t0 = t
        self.t_end = None
        self._rows = []
        self._last_tick = None           # log the first row straight away
        self.remaining = self._delay("data_taking_time")
        # Clear the on-screen graphs right as recording begins, so the plots
        # show exactly the window that will be exported to the CSV/Excel
        # (handled on the GUI thread in _redraw_plots).
        self.gui.pending_graph_reset = True
        # t0 lets the laptop place this instant on its own clock (live graph
        # marker; the authoritative copy travels with the recorded data).
        self._notify(Experiment.RECORDING, message, t0=t)

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

    def _drive_spooler(self, t: float, spooler) -> None:
        if self._mode("spooler_mode") == "open":
            spooler.dc_motor_open_loop_control(t)
        else:
            spooler.dc_motor_close_loop_control(t)

    def _drive_all(self, t: float, extruder, spooler, fan) -> None:
        """One sample tick with every system on."""
        self._drive_heater(t, extruder)
        self._drive_spooler(t, spooler)
        extruder.stepper_control_loop()
        self.gui.fan_enabled = True
        fan.control_loop()

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
    def _append_row(self, t: float, temp_new: bool, spool_new: bool) -> None:
        """Snapshot the latest values into one wide row (single time column)."""
        def last(lst):
            return lst[-1] if lst else ""
        self._rows.append([
            t - self.t0,
            last(Database.temperature_readings),
            last(Database.temperature_setpoint),
            last(Database.temperature_error),
            last(Database.temperature_pid_output),
            last(Database.temperature_kp),
            last(Database.temperature_ki),
            last(Database.temperature_kd),
            1 if temp_new else 0,
            self.gui.get_target_diameter(),
            last(Database.fan_duty_cycle),
            last(Database.extruder_rpm),
            last(Database.spooler_setpoint),
            last(Database.spooler_rpm),
            last(Database.spooler_kp),
            last(Database.spooler_ki),
            last(Database.spooler_kd),
            1 if spool_new else 0,
        ])

    @staticmethod
    def _num(value) -> str:
        """Format a value with a COMMA decimal separator (for Excel es-MX)."""
        if value == "" or value is None:
            return ""
        if isinstance(value, int):        # the 1/0 flag columns
            return str(value)
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
            duration = (self.t_end - self.t0) if self.t_end is not None else 0
            self.csv_meta = {
                "t0": self.t0,           # recording start, Pi clock (s)
                "t_end": self.t_end,     # recording end, Pi clock (s)
                "rows": len(self._rows),
                "rate_hz": (len(self._rows) / duration) if duration > 0 else 0,
                "boot": self.gui.laptop_link.boot_id,
            }
        except Exception as exc:
            print(f"[Experiment] CSV build error: {exc}")
            self.csv_result = None
            self.csv_meta = {}

    def data_payload(self):
        """Return the {type:data,...} message for the laptop, or None."""
        if not self.csv_result:
            return None
        b64 = base64.b64encode(self.csv_result.encode("utf-8")).decode("ascii")
        return {"type": "data", "format": "csv", "name": self.csv_name,
                "b64": b64, "meta": self.csv_meta}

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

    def announce(self) -> None:
        """Tell a (re)connected laptop where the run stands (incl. t0 while
        recording, so its graph and merge line up after a reconnect)."""
        extra = {"t0": self.t0} if self.phase == Experiment.RECORDING else {}
        text = self.status_line().replace("Experiment: ", "", 1)
        self._notify(self.phase, f"(connected) {text}", **extra)

    def _notify(self, phase: str, message: str, **extra) -> None:
        """Push a status update to the laptop (best-effort)."""
        msg = {
            "type": "status",
            "phase": phase,
            "remaining": round(self.remaining, 1),
            "message": message,
            "data_ready": bool(self.csv_result),
        }
        msg.update(extra)
        try:
            self.gui.laptop_link.send_message(msg)
        except Exception:
            pass
