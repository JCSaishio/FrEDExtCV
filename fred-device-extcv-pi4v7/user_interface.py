"""Graphical User Interface for the FrED device (external-CV variant, v7).

The fiber diameter is measured, graphed and recorded entirely on an external
computer (the laptop CV app). Since v7 it is not streamed to the Pi at all: the
Pi's CPU goes to the heater and spooler control loops and to their two graphs
(DC Motor and Temperature). The laptop connects over WiFi only to send
experiments and commands (see ``laptop_link.py``).
"""
import time

from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QDoubleSpinBox,
                             QSlider, QPushButton, QMessageBox, QLineEdit,
                             QGroupBox, QVBoxLayout, QHBoxLayout, QFormLayout,
                             QScrollArea, QSizePolicy)
from PyQt5.QtCore import QTimer, Qt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

import stepper_config
from database import Database
from laptop_link import LaptopLink
from experiment import Experiment
from step_check import SKIP_ADVICE


class UserInterface():
    """Graphical User Interface Class"""

    # Upper limits of the spooling-motor PID gains (Kp, Ki, Kd). They bound the
    # spin boxes here and any experiment sent from the laptop.
    MOTOR_GAIN_LIMITS = (1.0, 15.0, 0.05)

    # Check Steps turns this many whole revolutions (the shaft mark only lines
    # up again after whole revolutions).
    STEP_CHECK_REVOLUTIONS = 1

    # Sampling rate limits (Hz) for the control loops and logged data.
    SAMPLE_RATE_LIMITS = (1.0, 100.0)

    BUTTON_STYLE = (
        "QPushButton { background-color: #3a3a3a; color: white; font-size: 14px;"
        " font-weight: bold; padding: 6px; border: 1px solid #222222;"
        " border-radius: 4px; }"
        "QPushButton:disabled { background-color: #b0b0b0; color: #e8e8e8;"
        " border: 1px solid #999999; }")

    STOP_BUTTON_STYLE = (
        "QPushButton { background-color: #8a1f1f; color: white; font-size: 14px;"
        " font-weight: bold; padding: 6px; border: 1px solid #5c0000;"
        " border-radius: 4px; }"
        "QPushButton:disabled { background-color: #b0b0b0; color: #e8e8e8;"
        " border: 1px solid #999999; }")

    # Disabled inputs go visibly lighter while a remote experiment owns the
    # hardware (set_controls_locked); applied window-wide so every spinbox,
    # text field and slider shows the same "locked" look.
    LOCKED_INPUT_STYLE = (
        "QDoubleSpinBox:disabled, QLineEdit:disabled {"
        " background-color: #ececec; color: #a8a8a8;"
        " border: 1px solid #cccccc; }")

    SLIDER_STYLE = (
        "QSlider::groove:horizontal { height: 6px; background: #c9c9c9;"
        " border-radius: 3px; }"
        "QSlider::sub-page:horizontal { background: #3a6ea5;"
        " border-radius: 3px; }"
        "QSlider::handle:horizontal { background: #3a6ea5;"
        " border: 1px solid #2c567f; width: 16px; margin: -6px 0;"
        " border-radius: 8px; }"
        "QSlider::groove:horizontal:disabled { background: #e6e6e6; }"
        "QSlider::sub-page:horizontal:disabled { background: #d5d5d5; }"
        "QSlider::handle:horizontal:disabled { background: #cfcfcf;"
        " border: 1px solid #bbbbbb; }")

    def __init__(self) -> None:
        self.app = QApplication([])
        self.window = QWidget()

        # Origin of the Pi's experiment clock (see now()).
        self.clock_origin = time.monotonic()

        # --- Control / device state flags --------------------------------- #
        self.device_started = False
        self.start_motor_calibration = False
        self.heater_open_loop_enabled = False
        self.dc_motor_open_loop_enabled = False
        self.dc_motor_close_loop_enabled = False
        self.break_level1_enabled = False
        self.break_level2_enabled = False
        self.break_level3_enabled = False

        # Per-subsystem stop/monitor state (read by the hardware-control loop)
        self.fan_enabled = True              # False -> fan held at 0% duty
        self.monitor_mode_enabled = False    # read-only: graph with no output
        self.stepper_enabled = False         # Start Stepper: its loop runs
        self.stepper_stop_requested = False  # one-shot: zero the stepper output
        self.step_check_request = None       # (revolutions, rpm) from Check Steps
        self.step_check_running = False      # set by the hardware thread
        self.step_check_result = None        # StepRun handed back when done
        self.heater_stop_requested = False   # one-shot: zero the heater output
        self.dc_motor_stop_requested = False # one-shot: zero the spooler output
        self.pending_graph_reset = False     # set by an experiment start
        self._controls_locked = False        # manual buttons disabled state

        # --- Plots (the diameter is graphed on the laptop) ----------------- #
        self.motor_plot = self.Plot("DC Spooling Motor", "Speed (RPM)")
        self.temperature_plot = self.Plot("Temperature", "Temperature (C)")

        # --- Controls (widgets created, laid out later) ------------------- #
        self._create_controls()

        # Effective-sampling-rate tracking (updated once a second in the GUI).
        self._rate_prev_t = time.time()
        self._rate_prev_counts = (0, 0, 0)   # (temp, spooler, loop) buffer sizes

        # --- Remote experiment controller (driven from the laptop) -------- #
        self.experiment = Experiment(self)

        # --- WiFi command link to the laptop (experiments, sync) ---------- #
        self.laptop_link = LaptopLink(self)

        # --- Assemble window ---------------------------------------------- #
        self._build_layout()
        self.window.setStyleSheet(self.LOCKED_INPUT_STYLE)
        self.window.setWindowTitle("MIT FrED - External CV (WiFi, v7)")
        self.window.setGeometry(80, 60, 1600, 1000)
        self.window.setMinimumSize(1200, 800)
        self.app.aboutToQuit.connect(self.laptop_link.close)

    def now(self) -> float:
        """The Pi's experiment clock (s): monotonic, so it never jumps when the
        system clock is set. The hardware loop timestamps every sample with it
        and the laptop's clock sync reads it (laptop_link.py)."""
        return time.monotonic() - self.clock_origin

    # ==================================================================== #
    # Widget creation
    # ==================================================================== #
    def _create_controls(self) -> None:
        """Create every control widget and store it as an attribute."""
        # Extrusion (stepper) motor speed
        self.extrusion_motor_speed = QDoubleSpinBox()
        self.extrusion_motor_speed.setMinimum(0.0)
        self.extrusion_motor_speed.setMaximum(20.0)
        self.extrusion_motor_speed.setValue(0.0)
        self.extrusion_motor_speed.setSingleStep(0.1)
        self.extrusion_motor_speed.setDecimals(2)

        # Temperature controls
        self.target_temperature_label = QLabel("Temperature Setpoint (C)")
        self.target_temperature = QSlider(Qt.Horizontal)
        self.target_temperature.setMinimum(65)
        self.target_temperature.setMaximum(150)
        self.target_temperature.setValue(95)
        self.target_temperature.setStyleSheet(self.SLIDER_STYLE)
        self.target_temperature.valueChanged.connect(self.update_temperature_slider_label)

        self.temperature_kp = self._make_spinbox(0.0, 2.0, 1.0, 0.001, 5)
        self.temperature_ki = self._make_spinbox(0.0, 2.0, 0.001, 0.001, 5)
        self.temperature_kd = self._make_spinbox(0.0, 2.0, 0.05, 0.001, 5)

        # Heater open-loop PWM
        self.heater_open_loop_pwm = self._make_spinbox(0, 100, 0, 1, 0)

        # Fan
        self.fan_duty_cycle_label = QLabel("Fan Duty Cycle (%)")
        self.fan_duty_cycle = QSlider(Qt.Horizontal)
        self.fan_duty_cycle.setMinimum(0)
        self.fan_duty_cycle.setMaximum(100)
        self.fan_duty_cycle.setValue(30)
        self.fan_duty_cycle.setStyleSheet(self.SLIDER_STYLE)
        self.fan_duty_cycle.valueChanged.connect(self.update_fan_slider_label)

        # DC (spooling) motor controls
        self.dc_motor_pwm = self._make_spinbox(0, 100, 0, 1, 0)
        self.motor_setpoint = self._make_spinbox(0, 60, 30, 1, 1)
        kp_max, ki_max, kd_max = self.MOTOR_GAIN_LIMITS
        self.motor_kp = self._make_spinbox(0, kp_max, 0.50, 0.01, 3)
        self.motor_ki = self._make_spinbox(0, ki_max, 0.50, 0.01, 3)
        self.motor_kd = self._make_spinbox(0, kd_max, 0.05, 0.001, 4)

        # Data export
        self.csv_filename = QLineEdit()
        self.csv_filename.setPlaceholderText("Enter a file name")

        # Sampling rate (Hz) the control loops target, and a live read-out of
        # the rate actually being recorded to the CSV buffers.
        self.sample_rate_hz = self._make_spinbox(*self.SAMPLE_RATE_LIMITS,
                                                 50, 1, 0)
        self.sampling_status_label = QLabel("Sampling: waiting for data...")
        self.sampling_status_label.setWordWrap(True)

        # Remote-experiment status (driven from the laptop)
        self.experiment_status_label = QLabel("Experiment: idle")
        self.experiment_status_label.setWordWrap(True)
        self.experiment_status_label.setStyleSheet(
            "font-weight: bold; color: #3a6ea5;")

        # Laptop link status
        self.connection_status_label = QLabel("Laptop link: starting...")
        self.connection_status_label.setWordWrap(True)

        # WiFi hotspot / connection details the laptop needs (filled in live)
        self.connection_info_label = QLabel("Reading network info...")
        self.connection_info_label.setWordWrap(True)
        self.connection_info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.connection_info_label.setStyleSheet(
            "background-color: #1e1e1e; color: #e0e0e0; font-size: 13px; "
            "padding: 8px; border: 1px solid #444444; border-radius: 4px;")

    @staticmethod
    def _make_spinbox(minimum, maximum, value, step, decimals) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setMinimum(minimum)
        box.setMaximum(maximum)
        box.setSingleStep(step)
        box.setDecimals(decimals)
        box.setValue(value)
        return box

    # ==================================================================== #
    # Layout
    # ==================================================================== #
    def _build_layout(self) -> None:
        root = QHBoxLayout()

        # ---- Left: the two graphs, stacked and enlarged ------------------ #
        plots_panel = QVBoxLayout()
        for plot in (self.motor_plot, self.temperature_plot):
            plot.setMinimumHeight(300)
            plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            plots_panel.addWidget(plot)
        root.addLayout(plots_panel, 3)

        # ---- Right: scrollable controls ---------------------------------- #
        controls = QVBoxLayout()
        controls.addWidget(self._build_link_group())
        controls.addWidget(self._build_graphs_group())
        controls.addWidget(self._build_monitor_group())
        controls.addWidget(self._build_temperature_group())
        controls.addWidget(self._build_spooler_group())
        controls.addWidget(self._build_extruder_group())
        controls.addWidget(self._build_fan_group())
        controls.addWidget(self._build_export_group())
        controls.addStretch(1)

        controls_host = QWidget()
        controls_host.setLayout(controls)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls_host)
        scroll.setMinimumWidth(440)
        scroll.setMaximumWidth(560)
        root.addWidget(scroll, 1)

        self.window.setLayout(root)

    def _build_link_group(self) -> QGroupBox:
        box = QGroupBox("Laptop Link (WiFi) - diameter is on the laptop")
        layout = QVBoxLayout()

        wifi_title = QLabel("Connect the laptop over WiFi:")
        wifi_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(wifi_title)
        layout.addWidget(self.connection_info_label)
        layout.addWidget(self.experiment_status_label)

        self.connection_status_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.connection_status_label)

        box.setLayout(layout)
        return box

    def _build_temperature_group(self) -> QGroupBox:
        box = QGroupBox("Extruder Heater (Temperature)")
        layout = QVBoxLayout()

        layout.addWidget(self.target_temperature_label)
        layout.addWidget(self.target_temperature)

        form = QFormLayout()
        form.addRow("Temperature Kp", self.temperature_kp)
        form.addRow("Temperature Ki", self.temperature_ki)
        form.addRow("Temperature Kd", self.temperature_kd)
        form.addRow("Heater Open Loop PWM (%)", self.heater_open_loop_pwm)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.start_device_btn = QPushButton("Start Temperature Close Loop")
        self.start_device_btn.setStyleSheet(self.BUTTON_STYLE)
        self.start_device_btn.clicked.connect(self.set_start_device)
        self.heater_open_btn = QPushButton("Start Heater Open Loop")
        self.heater_open_btn.setStyleSheet(self.BUTTON_STYLE)
        self.heater_open_btn.clicked.connect(self.set_heater_open_loop)
        buttons.addWidget(self.start_device_btn)
        buttons.addWidget(self.heater_open_btn)
        layout.addLayout(buttons)

        stop_heater = QPushButton("STOP Heater")
        stop_heater.setStyleSheet(self.STOP_BUTTON_STYLE)
        stop_heater.clicked.connect(self.set_stop_heater)
        layout.addWidget(stop_heater)

        box.setLayout(layout)
        return box

    def _build_spooler_group(self) -> QGroupBox:
        box = QGroupBox("Spooling DC Motor")
        layout = QVBoxLayout()

        form = QFormLayout()
        form.addRow("Motor Setpoint (RPM)", self.motor_setpoint)
        form.addRow("Motor Kp", self.motor_kp)
        form.addRow("Motor Ki", self.motor_ki)
        form.addRow("Motor Kd", self.motor_kd)
        form.addRow("DC Motor PWM (%)", self.dc_motor_pwm)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.motor_close_btn = QPushButton("Start Motor Close Loop")
        self.motor_close_btn.setStyleSheet(self.BUTTON_STYLE)
        self.motor_close_btn.clicked.connect(self.set_motor_close_loop)
        self.dc_open_btn = QPushButton("Start DC Motor Open Loop")
        self.dc_open_btn.setStyleSheet(self.BUTTON_STYLE)
        self.dc_open_btn.clicked.connect(self.set_dc_motor_open_loop)
        buttons.addWidget(self.motor_close_btn)
        buttons.addWidget(self.dc_open_btn)
        layout.addLayout(buttons)

        stop_motor = QPushButton("STOP Spooling Motor")
        stop_motor.setStyleSheet(self.STOP_BUTTON_STYLE)
        stop_motor.clicked.connect(self.set_stop_motor)
        layout.addWidget(stop_motor)

        box.setLayout(layout)
        return box

    def _build_extruder_group(self) -> QGroupBox:
        box = QGroupBox("Extrusion Motor (Stepper)")
        layout = QVBoxLayout()
        form = QFormLayout()
        form.addRow("Extrusion Motor Speed (RPM)", self.extrusion_motor_speed)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.start_stepper_btn = QPushButton("Start Stepper")
        self.start_stepper_btn.setStyleSheet(self.BUTTON_STYLE)
        self.start_stepper_btn.clicked.connect(self.set_start_stepper)
        self.check_steps_btn = QPushButton("Check Steps")
        self.check_steps_btn.setStyleSheet(self.BUTTON_STYLE)
        self.check_steps_btn.clicked.connect(self.request_step_check)
        buttons.addWidget(self.start_stepper_btn)
        buttons.addWidget(self.check_steps_btn)
        layout.addLayout(buttons)

        stop_stepper = QPushButton("STOP Stepper")
        stop_stepper.setStyleSheet(self.STOP_BUTTON_STYLE)
        stop_stepper.clicked.connect(self.set_stop_stepper)
        layout.addWidget(stop_stepper)

        box.setLayout(layout)
        return box

    def _build_fan_group(self) -> QGroupBox:
        box = QGroupBox("Cooling Fan")
        layout = QVBoxLayout()
        layout.addWidget(self.fan_duty_cycle_label)
        layout.addWidget(self.fan_duty_cycle)

        self.fan_toggle_btn = QPushButton("STOP Fan")
        self.fan_toggle_btn.setStyleSheet(self.STOP_BUTTON_STYLE)
        self.fan_toggle_btn.clicked.connect(self.toggle_fan)
        layout.addWidget(self.fan_toggle_btn)

        box.setLayout(layout)
        return box

    def _build_graphs_group(self) -> QGroupBox:
        box = QGroupBox("Graphs & Sampling")
        layout = QVBoxLayout()
        self.reset_graphs_btn = QPushButton("Reset Graphs")
        self.reset_graphs_btn.setStyleSheet(self.BUTTON_STYLE)
        self.reset_graphs_btn.clicked.connect(self.reset_graphs)
        layout.addWidget(self.reset_graphs_btn)
        note = QLabel("Reset clears the Temperature and DC Motor plots on "
                      "screen (logged CSV data is kept).")
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        form.addRow("Sampling rate (Hz)", self.sample_rate_hz)
        layout.addLayout(form)
        layout.addWidget(self.sampling_status_label)
        hint = QLabel("Rate of the temperature & spooler control loops - the "
                      "data is recorded at this same rate (an experiment sent "
                      "from the laptop sets its own). The line above shows the "
                      "rate actually achieved; if it falls short, lower it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888888;")
        layout.addWidget(hint)
        box.setLayout(layout)
        return box

    def _build_monitor_group(self) -> QGroupBox:
        box = QGroupBox("Passive Monitoring (read-only)")
        layout = QVBoxLayout()
        info = QLabel("Graph the heater temperature and spooler RPM with NO "
                      "output driven to the system (no heating, no motors). "
                      "Useful to watch how the temperature behaves on its own.")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.monitor_btn = QPushButton("Start Monitoring (no output)")
        self.monitor_btn.setStyleSheet(self.BUTTON_STYLE)
        self.monitor_btn.clicked.connect(self.toggle_monitor)
        layout.addWidget(self.monitor_btn)

        box.setLayout(layout)
        return box

    def _build_export_group(self) -> QGroupBox:
        box = QGroupBox("Data Export")
        layout = QVBoxLayout()
        layout.addWidget(self.csv_filename)
        self.download_csv_btn = QPushButton("Download CSV File")
        self.download_csv_btn.setStyleSheet(self.BUTTON_STYLE)
        self.download_csv_btn.clicked.connect(self.set_download_csv)
        layout.addWidget(self.download_csv_btn)
        box.setLayout(layout)
        return box

    # ==================================================================== #
    # Slot handlers
    # ==================================================================== #
    def update_temperature_slider_label(self, value) -> None:
        self.target_temperature_label.setText(f"Temperature: {value} C")

    def update_fan_slider_label(self, value) -> None:
        self.fan_duty_cycle_label.setText(f"Fan Duty Cycle: {value} %")

    def set_heater_open_loop(self) -> None:
        if self._block_if_monitoring():
            return
        if self.device_started:
            QMessageBox.warning(self.app.activeWindow(), "Control Error",
                "Cannot start open loop control while close loop is running.\n"
                "Please restart the program.")
            return
        self.heater_open_loop_enabled = not self.heater_open_loop_enabled
        state = "started" if self.heater_open_loop_enabled else "stopped"
        QMessageBox.information(self.app.activeWindow(),
            "Heater Control", f"Heater open loop control {state}.")

    def set_dc_motor_open_loop(self) -> None:
        if self._block_if_monitoring():
            return
        if self.dc_motor_close_loop_enabled:
            QMessageBox.warning(self.app.activeWindow(), "Control Error",
                "Cannot enable open loop control while close loop is active.\n"
                "Please restart the program.")
            return
        self.dc_motor_open_loop_enabled = not self.dc_motor_open_loop_enabled
        state = "started" if self.dc_motor_open_loop_enabled else "stopped"
        QMessageBox.information(self.app.activeWindow(),
            "DC Motor Control", f"DC Motor open loop control {state}.")

    def set_motor_close_loop(self) -> None:
        if self._block_if_monitoring():
            return
        if self.dc_motor_open_loop_enabled:
            QMessageBox.warning(self.app.activeWindow(), "Control Error",
                "Cannot start Close Loop while Open Loop is running.\n"
                "Please stop Open Loop control first.")
            return
        self.dc_motor_close_loop_enabled = not self.dc_motor_close_loop_enabled
        state = "started" if self.dc_motor_close_loop_enabled else "stopped"
        QMessageBox.information(self.app.activeWindow(),
            "Motor Control", f"Motor close loop control {state}.")

    def reset_graphs(self) -> None:
        """Clear the on-screen Temperature and DC Motor plots."""
        for plot in (self.temperature_plot, self.motor_plot):
            plot.reset()

    def get_sample_period(self) -> float:
        """Sampling/control period (seconds): the running experiment's
        ``sample_rate_hz`` if it sets one, else the Sampling-rate spinbox.

        Read live by the temperature and spooler control loops (and the
        experiment's data rows) so control and data share one frequency.
        """
        hz = self.experiment.override("sample_rate_hz")
        if hz is None:
            try:
                hz = float(self.sample_rate_hz.value())
            except Exception:
                return 0.02
        low, high = self.SAMPLE_RATE_LIMITS
        return 1.0 / min(max(hz, low), high)

    # ------------------------------------------------------------------ #
    # Setpoint / gain accessors. Return the running experiment's value when an
    # experiment is active, otherwise the manual widget value. The hardware
    # modules read through these so the same control code serves both manual
    # operation and a remote experiment.
    # ------------------------------------------------------------------ #
    def get_target_temperature(self) -> float:
        v = self.experiment.override("target_temperature")
        return v if v is not None else self.target_temperature.value()

    def get_temperature_pid(self):
        if self.experiment.override("temp_kp") is not None:
            return (self.experiment.override("temp_kp"),
                    self.experiment.override("temp_ki"),
                    self.experiment.override("temp_kd"))
        return (self.temperature_kp.value(), self.temperature_ki.value(),
                self.temperature_kd.value())

    def get_heater_pwm(self) -> float:
        v = self.experiment.override("heater_pwm")
        return v if v is not None else self.heater_open_loop_pwm.value()

    def get_motor_setpoint(self) -> float:
        v = self.experiment.override("motor_setpoint")
        return v if v is not None else self.motor_setpoint.value()

    def get_motor_pid(self):
        if self.experiment.override("motor_kp") is not None:
            # Hold experiment gains to the same limits as the spin boxes.
            gains = (self.experiment.override("motor_kp"),
                     self.experiment.override("motor_ki"),
                     self.experiment.override("motor_kd"))
            return tuple(min(max(g or 0.0, 0.0), limit)
                         for g, limit in zip(gains, self.MOTOR_GAIN_LIMITS))
        return (self.motor_kp.value(), self.motor_ki.value(),
                self.motor_kd.value())

    def get_dc_motor_pwm(self) -> float:
        v = self.experiment.override("dc_motor_pwm")
        return v if v is not None else self.dc_motor_pwm.value()

    def get_extrusion_speed(self) -> float:
        v = self.experiment.override("extrusion_speed")
        return v if v is not None else self.extrusion_motor_speed.value()

    def get_fan_duty(self) -> float:
        v = self.experiment.override("fan_duty")
        return v if v is not None else self.fan_duty_cycle.value()

    def get_target_diameter(self):
        """Target diameter of the running experiment (only logged; the
        diameter itself is measured on the laptop), or "" when none."""
        v = self.experiment.override("target_diameter")
        return v if v is not None else ""

    def set_start_device(self) -> None:
        if self._block_if_monitoring():
            return
        if self.heater_open_loop_enabled:
            QMessageBox.warning(self.app.activeWindow(), "Control Error",
                "Cannot start Close Loop while open loop control is running.\n"
                "Please restart the program.")
            return
        QMessageBox.information(self.app.activeWindow(), "Device Start",
            "Device is starting.")
        self.device_started = True

    # ------------------------------------------------------------------ #
    # Per-subsystem STOP buttons (stop without closing the program)
    # ------------------------------------------------------------------ #
    def set_stop_heater(self) -> None:
        """Turn the heater off (both open- and closed-loop heating)."""
        self.device_started = False
        self.heater_open_loop_enabled = False
        self.heater_stop_requested = True
        QMessageBox.information(self.app.activeWindow(),
            "Heater", "Heater stopped. Restart it with one of the heater "
            "buttons when you are ready.")

    def set_start_stepper(self) -> None:
        """Run the extrusion stepper at the Extrusion Motor Speed.

        The stepper has its own loop, independent of the heater loops, so it
        turns whether or not a heater loop is running."""
        if self._block_if_monitoring():
            return
        self.stepper_enabled = True
        note = ""
        if not (self.device_started or self.heater_open_loop_enabled):
            note = ("\n\nNote: no heater loop is running. With a cold barrel "
                    "the screw pushes against solid plastic.")
        QMessageBox.information(self.app.activeWindow(), "Stepper",
            f"Extrusion stepper started at "
            f"{self.extrusion_motor_speed.value():.2f} RPM. Change the "
            f"Extrusion Motor Speed at any time; STOP Stepper stops it." + note)

    def set_stop_stepper(self) -> None:
        """Stop the extrusion stepper (its speed setting is kept)."""
        self.stepper_enabled = False
        self.stepper_stop_requested = True
        QMessageBox.information(self.app.activeWindow(),
            "Stepper", "Extrusion stepper stopped. Press Start Stepper to run "
            "it again.")

    def request_step_check(self) -> None:
        """Check Steps: turn exactly STEP_CHECK_REVOLUTIONS at the Extrusion
        Motor Speed, so a mark on the shaft shows whether any step was
        skipped (step_check.py)."""
        if self._block_if_monitoring():
            return
        if self.step_check_request is not None or self.step_check_running:
            QMessageBox.information(self.app.activeWindow(), "Check Steps",
                "A step check is already running (STOP Stepper aborts it).")
            return
        rpm = self.extrusion_motor_speed.value()
        if rpm <= 0.0:
            QMessageBox.warning(self.app.activeWindow(), "Check Steps",
                "Set the Extrusion Motor Speed (RPM) to test first.")
            return
        revs = self.STEP_CHECK_REVOLUTIONS
        steps = revs * stepper_config.pulses_per_revolution()
        answer = QMessageBox.question(self.app.activeWindow(), "Check Steps",
            f"The stepper will turn exactly {revs} revolution ({steps} steps "
            f"at 1/{stepper_config.MICROSTEPS} microstepping) at {rpm:.2f} "
            f"RPM, which takes about {revs * 60.0 / rpm:.0f} s.\n\n"
            "1. Put a mark on the motor shaft or coupling and note what it "
            "points at.\n"
            "2. Press Yes and watch the motor.\n"
            "3. At the end the mark must point exactly where it started.\n\n"
            "The normal stepper pauses during the check. STOP Stepper aborts "
            "it.", QMessageBox.Yes | QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.step_check_request = (revs, rpm)

    def _show_step_check_result(self, result) -> None:
        """Ask how the mark ended up after a Check Steps run (GUI thread)."""
        print(f"[Check Steps] {result.summary()} {result.timing_text()}")
        window = self.app.activeWindow()
        if result.aborted:
            QMessageBox.information(window, "Check Steps", result.summary())
            return
        answer = QMessageBox.question(window, "Check Steps",
            f"{result.summary()}\n{result.timing_text()}\n\n"
            "Is the mark back exactly where it started?",
            QMessageBox.Yes | QMessageBox.No)
        if answer == QMessageBox.Yes:
            print(f"[Check Steps] no skipped steps at {result.rpm:g} RPM")
            QMessageBox.information(window, "Check Steps",
                f"No skipped steps at {result.rpm:g} RPM.")
        else:
            print(f"[Check Steps] SKIPPED steps at {result.rpm:g} RPM")
            QMessageBox.warning(window, "Check Steps",
                f"The motor skipped steps at {result.rpm:g} RPM.\n\n"
                f"{SKIP_ADVICE}\n\nFor a speed sweep, close this program "
                "and run:  bash check_stepper.sh")

    def set_stop_motor(self) -> None:
        """Stop the DC spooling motor (both open- and closed-loop)."""
        self.dc_motor_open_loop_enabled = False
        self.dc_motor_close_loop_enabled = False
        self.dc_motor_stop_requested = True
        QMessageBox.information(self.app.activeWindow(),
            "Spooling Motor", "Spooling motor stopped. Restart it with one of "
            "the motor buttons when you are ready.")

    def toggle_fan(self) -> None:
        """Stop or resume the cooling fan without losing its slider setting."""
        self.fan_enabled = not self.fan_enabled
        if self.fan_enabled:
            self.fan_toggle_btn.setText("STOP Fan")
            self.fan_toggle_btn.setStyleSheet(self.STOP_BUTTON_STYLE)
        else:
            self.fan_toggle_btn.setText("Start Fan")
            self.fan_toggle_btn.setStyleSheet(self.BUTTON_STYLE)

    # ------------------------------------------------------------------ #
    # Passive monitoring (graph temperature / RPM with no output)
    # ------------------------------------------------------------------ #
    def _block_if_monitoring(self) -> bool:
        """Refuse to start a control loop while monitor mode is active."""
        if self.monitor_mode_enabled:
            QMessageBox.warning(self.app.activeWindow(), "Monitoring active",
                "Passive Monitoring is on (no output is driven). Stop "
                "monitoring first, then start the control loop.")
            return True
        return False

    def toggle_monitor(self) -> None:
        """Enter/leave read-only monitoring of temperature and spooler RPM."""
        self.monitor_mode_enabled = not self.monitor_mode_enabled
        if self.monitor_mode_enabled:
            # Make sure nothing is driving the system before we observe it.
            self.device_started = False
            self.heater_open_loop_enabled = False
            self.dc_motor_open_loop_enabled = False
            self.dc_motor_close_loop_enabled = False
            self.stepper_enabled = False
            self.heater_stop_requested = True
            self.stepper_stop_requested = True
            self.dc_motor_stop_requested = True
            self.monitor_btn.setText("Stop Monitoring")
            self.monitor_btn.setStyleSheet(self.STOP_BUTTON_STYLE)
            QMessageBox.information(self.app.activeWindow(), "Monitoring",
                "Monitoring started: temperature and spooler RPM are graphed "
                "with NO output driven to the system.")
        else:
            self.monitor_btn.setText("Start Monitoring (no output)")
            self.monitor_btn.setStyleSheet(self.BUTTON_STYLE)
            QMessageBox.information(self.app.activeWindow(), "Monitoring",
                "Monitoring stopped.")

    def set_download_csv(self) -> None:
        QMessageBox.information(self.app.activeWindow(), "Download CSV",
                                "Downloading CSV file.")
        Database.generate_csv(self.csv_filename.text())

    def show_message(self, title: str, message: str) -> None:
        QMessageBox.information(self.app.activeWindow(), title, message)

    # ==================================================================== #
    # Plot widget
    # ==================================================================== #
    class Plot(FigureCanvas):
        # The plots are redrawn by a timer on the GUI thread every this many ms
        # (~10 FPS). The hardware-control thread only APPENDS data (microseconds)
        # and never touches matplotlib, so a high sampling rate is not slowed by
        # the (expensive) canvas redraw and every sample reaches the CSV buffers.
        REDRAW_INTERVAL_MS = 100
        # At most this many points are drawn per line: a long run at 50 Hz has
        # tens of thousands, and redrawing them all every 100 ms would take
        # CPU from the control loops. Only the drawing is thinned out - every
        # sample is still recorded.
        MAX_DRAWN_POINTS = 1500

        def __init__(self, title: str, y_label: str) -> None:
            self.figure = Figure()
            self.axes = self.figure.add_subplot(111)
            super(UserInterface.Plot, self).__init__(self.figure)
            self.title = title
            self.axes.set_title(title)
            self.axes.set_xlabel("Time (s)")
            self.axes.set_ylabel(y_label)
            self.progress_line, = self.axes.plot([], [], lw=2, label=title)
            self.setpoint_line, = self.axes.plot([], [], lw=2, color='r',
                                                 label=f'Target {title}')
            self.axes.legend()
            self.x_data = []
            self.y_data = []
            self.setpoint_data = []
            self._latest_y = 0.0
            self._dirty = False

        def update_plot(self, x: float, y: float, setpoint: float) -> None:
            """Append a sample. Called from the hardware thread - APPEND ONLY,
            no matplotlib calls here (those happen in redraw() on the GUI
            thread). Keeps this microsecond-cheap so sampling stays at full rate.
            """
            self.x_data.append(x)
            self.y_data.append(y)
            self.setpoint_data.append(setpoint)
            self._latest_y = y
            self._dirty = True

        def redraw(self) -> None:
            """Repaint the canvas from the collected data. GUI thread only."""
            if not self._dirty:
                return
            self._dirty = False
            # Snapshot to a consistent length: the hardware thread may append
            # between these reads, and lists only ever grow (except reset()).
            n = min(len(self.x_data), len(self.y_data), len(self.setpoint_data))
            if n == 0:
                return
            step = (n - 1) // self.MAX_DRAWN_POINTS + 1
            idx = list(range(0, n, step))
            if idx[-1] != n - 1:
                idx.append(n - 1)          # always draw the newest sample
            xs = [self.x_data[i] for i in idx]
            ys = [self.y_data[i] for i in idx]
            sps = [self.setpoint_data[i] for i in idx]
            self.progress_line.set_label(f"{self.title}: {self._latest_y:.2f}")
            self.axes.legend()
            self.progress_line.set_data(xs, ys)
            self.setpoint_line.set_data(xs, sps)
            self.axes.relim()
            self.axes.autoscale_view()
            self.draw()

        def reset(self) -> None:
            """Clear the plotted data so the graph starts fresh on screen."""
            # Rebind to new lists (atomic) rather than mutating, since the
            # hardware-control thread may be appending concurrently.
            self.x_data = []
            self.y_data = []
            self.setpoint_data = []
            self._dirty = False
            self.progress_line.set_data([], [])
            self.setpoint_line.set_data([], [])
            self.progress_line.set_label(self.title)
            self.axes.legend()
            self.axes.relim()
            self.axes.autoscale_view()
            self.draw()

    # ==================================================================== #
    # GUI lifecycle
    # ==================================================================== #
    def _update_connection_status(self) -> None:
        info = self.laptop_link.connection_info()
        ips = info["all_ips"] or [info["ip"]]
        ip_line = info["ip"]
        if len(ips) > 1:
            others = ", ".join(ip for ip in ips if ip != info["ip"])
            ip_line = f"{info['ip']}   (also: {others})"
        client_line = (f"Laptop connected: {info['client']}"
                       if info["client"] else "Laptop: not connected yet")
        self.connection_info_label.setText(
            f"1) Join WiFi network:  {info['ssid']}\n"
            f"     password:  {info['password']}\n"
            f"2) In the laptop app, connect to:\n"
            f"     IP:  {ip_line}\n"
            f"     Port:  {info['port']}\n"
            f"{client_line}")

        self.experiment_status_label.setText(self.experiment.status_line())

        # Lock the manual buttons while an experiment owns the hardware.
        locked = self.experiment.is_active()
        if locked != self._controls_locked:
            self._controls_locked = locked
            self.set_controls_locked(locked)

        # Keep the fan toggle in step with fan_enabled: an experiment abort
        # switches the fan off from the hardware/network side, not the button.
        fan_text = "STOP Fan" if self.fan_enabled else "Start Fan"
        if self.fan_toggle_btn.text() != fan_text:
            self.fan_toggle_btn.setText(fan_text)
            self.fan_toggle_btn.setStyleSheet(
                self.STOP_BUTTON_STYLE if self.fan_enabled
                else self.BUTTON_STYLE)

        self.connection_status_label.setText(self.laptop_link.status_text())
        color = "green" if self.laptop_link.connected else "red"
        self.connection_status_label.setStyleSheet(
            f"font-weight: bold; color: {color};")

        # A finished Check Steps run, handed back by the hardware thread
        # (cleared first: the dialog below runs its own event loop).
        result = self.step_check_result
        if result is not None:
            self.step_check_result = None
            self._show_step_check_result(result)

    def _redraw_plots(self) -> None:
        """Repaint all plots on the GUI thread (driven by a QTimer)."""
        # An experiment start asks (from another thread) for a clean graph.
        if self.pending_graph_reset:
            self.pending_graph_reset = False
            self.reset_graphs()
        for plot in (self.motor_plot, self.temperature_plot):
            plot.redraw()

    def set_controls_locked(self, locked: bool) -> None:
        """Disable/enable EVERY manual control while a remote experiment runs.

        Only the red STOP Heater / STOP Spooling Motor / STOP Stepper buttons
        stay live (they abort the experiment). Everything else - start buttons,
        PID gain boxes, setpoint spinboxes, the temperature and fan sliders,
        the sampling rate, file name and export/reset buttons - is disabled in
        the system and shown in lighter colors (see the :disabled rules in
        BUTTON_STYLE, LOCKED_INPUT_STYLE and SLIDER_STYLE)."""
        controls = (
            # buttons
            self.start_device_btn,
            self.heater_open_btn, self.motor_close_btn, self.dc_open_btn,
            self.start_stepper_btn, self.check_steps_btn,
            self.monitor_btn, self.fan_toggle_btn, self.reset_graphs_btn,
            self.download_csv_btn,
            # setpoints / gains / inputs
            self.extrusion_motor_speed,
            self.target_temperature, self.temperature_kp, self.temperature_ki,
            self.temperature_kd, self.heater_open_loop_pwm,
            self.fan_duty_cycle, self.dc_motor_pwm, self.motor_setpoint,
            self.motor_kp, self.motor_ki, self.motor_kd,
            self.csv_filename, self.sample_rate_hz)
        for widget in controls:
            widget.setEnabled(not locked)

    def _update_rate_label(self) -> None:
        """Show the effective sampling rate actually being written to the CSV
        buffers, so the user can confirm the rate and adjust it if needed."""
        now = time.time()
        dt = now - self._rate_prev_t
        if dt <= 0:
            return
        temp_n = len(Database.temperature_timestamps)
        spool_n = len(Database.spooler_timestamps)
        loop_n = len(Database.time_readings)
        p_temp, p_spool, p_loop = self._rate_prev_counts
        temp_hz = (temp_n - p_temp) / dt
        spool_hz = (spool_n - p_spool) / dt
        loop_hz = (loop_n - p_loop) / dt
        self._rate_prev_t = now
        self._rate_prev_counts = (temp_n, spool_n, loop_n)

        period = self.get_sample_period()
        target_hz = 1.0 / period if period > 0 else 0.0
        self.sampling_status_label.setText(
            f"Target {target_hz:.0f} Hz  |  recorded to CSV: temp "
            f"{temp_hz:.0f} Hz, spooler {spool_hz:.0f} Hz  |  loop {loop_hz:.0f} Hz")
        active = max(temp_hz, spool_hz)
        if active <= 0.5:
            color = "#888888"   # nothing is recording right now
        elif active >= 0.8 * target_hz:
            color = "green"     # keeping up with the requested rate
        else:
            color = "orange"    # recording, but slower than requested
        self.sampling_status_label.setStyleSheet(color and f"color: {color};")

    def start_gui(self) -> None:
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self._update_connection_status)
        self.status_timer.start(500)

        # Redraw the plots on the GUI thread (the hardware thread only appends).
        self.plot_timer = QTimer()
        self.plot_timer.timeout.connect(self._redraw_plots)
        self.plot_timer.start(self.Plot.REDRAW_INTERVAL_MS)

        # Report the effective (CSV) sampling rate once a second.
        self.rate_timer = QTimer()
        self.rate_timer.timeout.connect(self._update_rate_label)
        self.rate_timer.start(1000)

        self.window.show()
        self.app.exec_()
