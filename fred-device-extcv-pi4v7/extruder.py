"""File to control the extrusion process"""
import time
import math
from collections import deque

import RPi.GPIO as GPIO
import busio
import board
import digitalio
import adafruit_mcp3xxx.mcp3008 as MCP
from adafruit_mcp3xxx.analog_in import AnalogIn

import stepper_config
from database import Database
from step_check import StepCheck
from user_interface import UserInterface

class Thermistor:
    """Constants and util functions for the thermistor"""
    REFERENCE_TEMPERATURE = 298.15 # K
    RESISTANCE_AT_REFERENCE = 100000 # Ω
    BETA_COEFFICIENT = 3977 # K
    VOLTAGE_SUPPLY = 3.3 # V
    RESISTOR = 100000 # Ω
    READINGS_TO_AVERAGE = 10
    # The control temperature is the mean of the readings from the last
    # AVERAGE_WINDOW seconds: 10 readings x the original 0.1 s period, so at
    # 10 Hz it is exactly the original 10-reading average, and at a higher
    # sampling rate the signal stays as smooth instead of averaging only the
    # last 0.2 s.
    AVERAGE_WINDOW = 1.0 # s
    _window = deque()    # (time, temperature) readings inside AVERAGE_WINDOW

    @classmethod
    def get_temperature(cls, voltage: float, current_time: float = None) -> float:
        """Get the average temperature from the voltage using Steinhart-Hart
        equation"""
        if voltage < 0.0001 or voltage >= cls.VOLTAGE_SUPPLY:  # Prevenir división por cero
            return 0
        resistance = ((cls.VOLTAGE_SUPPLY - voltage) * cls.RESISTOR )/ voltage
        ln = math.log(resistance / cls.RESISTANCE_AT_REFERENCE)
        temperature = (1 / ((ln / cls.BETA_COEFFICIENT) + (1 / cls.REFERENCE_TEMPERATURE))) - 273.15
        Database.temperature_readings.append(temperature)
        if current_time is not None:
            window = cls._window
            window.append((current_time, temperature))
            while window[0][0] <= current_time - cls.AVERAGE_WINDOW:
                window.popleft()
            return sum(value for _, value in window) / len(window)
        average_temperature = 0
        if len(Database.temperature_readings) > cls.READINGS_TO_AVERAGE:
            # Get last constant readings
            average_temperature = (sum(Database.temperature_readings
                                      [-cls.READINGS_TO_AVERAGE:]) /
                                      cls.READINGS_TO_AVERAGE)
        else:
            average_temperature = (sum(Database.temperature_readings) /
                                   len(Database.temperature_readings))
        return average_temperature

class Extruder:
    """Controller of the extrusion process: the heater and stepper motor"""
    HEATER_PIN = 6
    # The stepper pins and the microstep resolution live in stepper_config.py
    # (shared with the skipped-step checker). Change MICROSTEPS there.
    DIRECTION_PIN = stepper_config.DIRECTION_PIN
    STEP_PIN = stepper_config.STEP_PIN

    DEFAULT_DIAMETER = 0.35
    MINIMUM_DIAMETER = 0.3
    MAXIMUM_DIAMETER = 0.6
    STEPS_PER_REVOLUTION = stepper_config.STEPS_PER_REVOLUTION
    DEFAULT_RPM = 0.6 # TODO: Delay is not being used, will be removed temporarily
    # The temperature loops run at the interface's Sampling rate
    # (gui.get_sample_period()), the same rate the data is logged at. The PID
    # sees the Thermistor.AVERAGE_WINDOW (1 s) mean, so its input is as smooth
    # as with the original 0.1 s period whatever rate is chosen.
    MAX_OUTPUT = 100
    MIN_OUTPUT = 0

    def __init__(self, gui: UserInterface) -> None:
        self.gui = gui
        self.speed = 0.0
        self.duty_cycle = 0.0
        self.channel_0 = None
        
        GPIO.setup(Extruder.HEATER_PIN, GPIO.OUT)
        GPIO.setup(Extruder.DIRECTION_PIN, GPIO.OUT)
        GPIO.setup(Extruder.STEP_PIN, GPIO.OUT)
        self.set_motor_direction(False)

        # Microstep resolution (stepper_config.MICROSTEPS, 1/16): all three
        # mode pins are driven, whatever an earlier program left them at.
        stepper_config.set_mode_pins(GPIO)

        # PWM Setup
        self._start_step_pwm()
        self.current_rpm = 0.0  # speed the step PWM is currently set to
        self.previous_stepper_time = 0.0  # last stepper loop tick
        self.step_check = None  # running Check Steps (step_check.StepCheck)

        self.heater_pwm = GPIO.PWM(Extruder.HEATER_PIN, 1)  
        self.heater_pwm.start(0)  
    
        self.initialize_thermistor()
        self.current_diameter = 0.0
        self.diameter_setpoint = Extruder.DEFAULT_DIAMETER
        
        # Control parameters
        self.previous_time = 0.0
        self.previous_error = 0.0
        self.integral = 0.0

    def initialize_thermistor(self):
        """Initialize the SPI for thermistor temperature readings"""
        spi = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)

        # Create the cs (chip select)
        cs = digitalio.DigitalInOut(board.D8)

        # Create the mcp object
        mcp = MCP.MCP3008(spi, cs)

        # Create analog inputs connected to the input pins on the MCP3008
        self.channel_0 = AnalogIn(mcp, MCP.P0)

    def set_motor_direction(self, clockwise: bool) -> None:
        """Set motor direction"""
        GPIO.output(Extruder.DIRECTION_PIN, not clockwise)

    def set_motor_speed(self, rpm: float) -> None:
        """Set motor speed in RPM (accounting for microstepping).

        The driver needs MICROSTEPS step pulses per full motor step, so the
        pulse frequency is multiplied to keep the physical RPM requested.
        """
        frequency = stepper_config.step_frequency(rpm)
        if frequency <= 0:
            return
        self.pwm.ChangeFrequency(frequency)
        self.pwm_frequency = frequency
        self.pwm.ChangeDutyCycle(50)

    def _start_step_pwm(self) -> None:
        """Create the step PWM, idle (0 % duty)."""
        self.pwm = GPIO.PWM(Extruder.STEP_PIN, 1000)
        self.pwm.start(0)
        self.pwm_frequency = 1000.0

    @staticmethod
    def _log_rpm(timestamp: float, rpm: float) -> None:
        """Log a stepper sample on the Pi clock (kept in time order)."""
        if (Database.extruder_timestamps
                and timestamp < Database.extruder_timestamps[-1]):
            timestamp = Database.extruder_timestamps[-1]
        Database.extruder_timestamps.append(timestamp)
        Database.extruder_rpm.append(rpm)

    def stepper_control_loop(self, current_time: float) -> None:
        """Run the stepper at the Extrusion Motor Speed setpoint.

        Its own loop, independent of the heater: it runs at the interface's
        Sampling rate on its own timing, like the temperature and spooler
        loops, whether or not a heater loop is on. In an experiment it is
        driven by the run's tick (same timestamp as the recorded row).
        """
        if current_time - self.previous_stepper_time <= self.gui.get_sample_period():
            return
        if self.step_check is not None:
            return  # Check Steps owns the STEP pin until it finishes
        self.previous_stepper_time = current_time
        try:
            setpoint_rpm = self.gui.get_extrusion_speed()
            # Reprogram the step PWM only when the speed changes (as MIT's
            # StepperMotor.set_speed does): resetting it on every pass could
            # swallow step pulses.
            if setpoint_rpm != self.current_rpm:
                if setpoint_rpm > 0.0:
                    self.set_motor_speed(setpoint_rpm)
                else:
                    self.pwm.ChangeDutyCycle(0)
                self.current_rpm = setpoint_rpm
            self._log_rpm(current_time, setpoint_rpm)
        except Exception as e:
            print(f"Error in stepper control loop: {e}")
            self.gui.show_message("Error", "Stepper control loop error")

    def temperature_control_loop(self, current_time: float) -> None:
        """Closed loop control of the temperature of the extruder for desired diameter"""
        if current_time - self.previous_time <= self.gui.get_sample_period():
            return
        try:
            target_temperature = self.gui.get_target_temperature()
            kp, ki, kd = self.gui.get_temperature_pid()

            delta_time = current_time - self.previous_time
            self.previous_time = current_time
            temperature = Thermistor.get_temperature(self.channel_0.voltage,
                                                     current_time)
            
            error = target_temperature - temperature
            self.integral += error * delta_time
            derivative = (error - self.previous_error) / delta_time
            self.previous_error = error
            output = kp * error + ki * self.integral + kd * derivative
            if output > Extruder.MAX_OUTPUT:
                output = Extruder.MAX_OUTPUT
            elif output < Extruder.MIN_OUTPUT:
                output = Extruder.MIN_OUTPUT
            
            self.heater_pwm.ChangeDutyCycle(output)
            
            self.gui.temperature_plot.update_plot(current_time, temperature,target_temperature)
            
            Database.temperature_timestamps.append(current_time)
            Database.temperature_delta_time.append(delta_time)
            Database.temperature_setpoint.append(target_temperature)
            Database.temperature_error.append(error)
            Database.temperature_pid_output.append(output)
            Database.temperature_kp.append(kp)
            Database.temperature_ki.append(ki)
            Database.temperature_kd.append(kd)
        except Exception as e:
            print(f"Error in temperature control loop: {e}")
            self.gui.show_message("Error", "Error in temperature control loop",
                                  "Please restart the program.")
            
    
    def temperature_open_loop_control(self, current_time: float) -> None:
        """Open loop PWM control of the heater"""
        if current_time - self.previous_time <= self.gui.get_sample_period():
            return
            
        try:
            pwm_value = self.gui.get_heater_pwm()
            delta_time = current_time - self.previous_time
            self.previous_time = current_time
            temperature = Thermistor.get_temperature(self.channel_0.voltage,
                                                     current_time)

            # Configurar PWM para el heater
            if not hasattr(self, 'heater_pwm'):
                self.heater_pwm = GPIO.PWM(Extruder.HEATER_PIN, 1)  # 1kHz frequency
                self.heater_pwm.start(0)

            # Actualizar duty cycle del PWM
            self.heater_pwm.ChangeDutyCycle(pwm_value)

            # Actualizar gráfica
            self.gui.temperature_plot.update_plot(current_time, temperature, 0)

            # Almacenar datos
            Database.temperature_timestamps.append(current_time)
            Database.temperature_delta_time.append(delta_time)
            Database.temperature_setpoint.append(0)  # No hay setpoint en lazo abierto
            Database.temperature_error.append(0)     # No hay error en lazo abierto
            Database.temperature_pid_output.append(pwm_value)
            Database.temperature_kp.append(0)
            Database.temperature_ki.append(0)
            Database.temperature_kd.append(0)

        except Exception as e:
            print(f"Error in temperature open loop control: {e}")
            self.gui.show_message("Error", "Error in temperature open loop control")

    # ---------------------------------------------------------------- #
    # Manual stop helpers (called from the hardware loop on UI request)
    # ---------------------------------------------------------------- #
    def stop_heater(self) -> None:
        """Turn the heater fully off and clear the PID state."""
        try:
            self.heater_pwm.ChangeDutyCycle(0)
            self.integral = 0.0
            self.previous_error = 0.0
        except Exception as e:
            print(f"Error stopping heater: {e}")

    def stop_stepper(self) -> None:
        """Stop the extrusion stepper (zero the step PWM, abort Check Steps)."""
        try:
            # Forget the running speed so the next stepper_control_loop
            # restarts the PWM even if the setpoint is unchanged.
            self.current_rpm = 0.0
            if self.step_check is not None:
                self.step_check.abort()
            else:
                self.pwm.ChangeDutyCycle(0)
            self._log_rpm(self.gui.now(), 0.0)
        except Exception as e:
            print(f"Error stopping stepper: {e}")

    # ---------------------------------------------------------------- #
    # Check Steps: skipped-step check (see step_check.py)
    # ---------------------------------------------------------------- #
    def start_step_check(self, revolutions: float, rpm: float) -> None:
        """Turn exactly ``revolutions`` at ``rpm`` in a background thread.

        The software PWM cannot count pulses, so it hands the STEP pin over:
        stop() ends its thread within one PWM period, which the check waits
        out before its first pulse. The stepper loop pauses meanwhile, and
        service_step_check() restarts the PWM when the check ends.
        """
        if self.step_check is not None:
            return
        release_wait = 2.0 / self.pwm_frequency + 0.01
        self.pwm.ChangeDutyCycle(0)
        self.pwm.stop()
        self.pwm = None
        self.current_rpm = 0.0
        self.gui.step_check_running = True
        self._log_rpm(self.gui.now(), rpm)
        self.step_check = StepCheck(GPIO, revolutions, rpm,
                                    start_delay=release_wait)
        self.step_check.start()

    def cancel_step_check(self) -> None:
        """Abort a running Check Steps (e.g. an experiment takes over)."""
        if self.step_check is not None:
            self.step_check.abort()

    def service_step_check(self) -> None:
        """Hardware loop: once a Check Steps run has ended, restart the step
        PWM and hand the result to the interface."""
        check = self.step_check
        if check is None or check.is_alive():
            return
        try:
            self._start_step_pwm()
        except RuntimeError:
            return  # the old PWM thread still holds the pin: retry next pass
        self.step_check = None
        self.current_rpm = 0.0  # the stepper loop re-applies its setpoint
        self._log_rpm(self.gui.now(), 0.0)
        self.gui.step_check_result = check.result
        self.gui.step_check_running = False

    def monitor_temperature(self, current_time: float) -> None:
        """Read and graph the temperature WITHOUT driving the heater.

        Used by the interface's monitor mode to observe how the heater
        temperature behaves on its own, with no control output applied.
        """
        if current_time - self.previous_time <= self.gui.get_sample_period():
            return
        try:
            delta_time = current_time - self.previous_time
            self.previous_time = current_time
            temperature = Thermistor.get_temperature(self.channel_0.voltage,
                                                     current_time)

            # Guarantee no heater output while monitoring.
            self.heater_pwm.ChangeDutyCycle(0)

            # Plot temperature with a zero setpoint (no target is being driven).
            self.gui.temperature_plot.update_plot(current_time, temperature, 0)

            Database.temperature_timestamps.append(current_time)
            Database.temperature_delta_time.append(delta_time)
            Database.temperature_setpoint.append(0)
            Database.temperature_error.append(0)
            Database.temperature_pid_output.append(0)
            Database.temperature_kp.append(0)
            Database.temperature_ki.append(0)
            Database.temperature_kd.append(0)
        except Exception as e:
            print(f"Error in temperature monitor: {e}")
                 
