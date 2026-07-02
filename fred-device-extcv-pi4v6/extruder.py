"""File to control the extrusion process"""
import time
import math
import RPi.GPIO as GPIO
import busio
import board
import digitalio
import adafruit_mcp3xxx.mcp3008 as MCP
from adafruit_mcp3xxx.analog_in import AnalogIn

from database import Database
from user_interface import UserInterface

class Thermistor:
    """Constants and util functions for the thermistor"""
    REFERENCE_TEMPERATURE = 298.15 # K
    RESISTANCE_AT_REFERENCE = 100000 # Ω
    BETA_COEFFICIENT = 3977 # K
    VOLTAGE_SUPPLY = 3.3 # V
    RESISTOR = 100000 # Ω
    READINGS_TO_AVERAGE = 10

    @classmethod
    def get_temperature(cls, voltage: float) -> float:
        """Get the average temperature from the voltage using Steinhart-Hart 
        equation"""
        if voltage < 0.0001 or voltage >= cls.VOLTAGE_SUPPLY:  # Prevenir división por cero
            return 0
        resistance = ((cls.VOLTAGE_SUPPLY - voltage) * cls.RESISTOR )/ voltage
        ln = math.log(resistance / cls.RESISTANCE_AT_REFERENCE)
        temperature = (1 / ((ln / cls.BETA_COEFFICIENT) + (1 / cls.REFERENCE_TEMPERATURE))) - 273.15
        Database.temperature_readings.append(temperature)
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
    DIRECTION_PIN = 16
    STEP_PIN = 20

    # --- Microstepping (DRV8825) ------------------------------------------- #
    # The DRV8825 selects its microstep resolution from three mode pins
    # M0, M1, M2. On this PCB only M2 is wired to a GPIO; M0 and M1 are left
    # floating, and the DRV8825's internal pull-down resistors hold them LOW.
    # With M0 = M1 = LOW the relevant part of the truth table is:
    #     M2 = LOW   -> full step   (noisy: this is the vibration we are fixing)
    #     M2 = HIGH  -> 1/16 step   (smooth)
    # So we drive M2 HIGH for 1/16 microstepping and multiply the STEP pulse
    # frequency by MICROSTEP_FACTOR below so the requested RPM is unchanged.
    #
    # Confirmed by continuity test: the DRV8825 M2 pin is wired to BCM GPIO22
    # (physical header pin 15) on this PCB.
    MICROSTEP_M2_PIN = 22   # BCM GPIO the DRV8825 M2 pin is wired to
    MICROSTEP_FACTOR = 16   # 1/16 step when M2 = HIGH and M0 = M1 = LOW

    DEFAULT_DIAMETER = 0.35
    MINIMUM_DIAMETER = 0.3
    MAXIMUM_DIAMETER = 0.6
    STEPS_PER_REVOLUTION = 200
    DEFAULT_RPM = 0.6 # TODO: Delay is not being used, will be removed temporarily
    # Temperature control period (s) = the ORIGINAL 0.1 s / 10 Hz. The PID gains
    # were tuned at this rate; running the loop faster (v5/v6 tried 50 Hz) made
    # the control unstable, so the control loops use this fixed period again.
    # Data logging for experiments is handled separately at the user's chosen
    # rate, so this does NOT cap how dense the recorded data is.
    SAMPLE_TIME = 0.1
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

        # Enable 1/16 microstepping on the DRV8825 by driving M2 HIGH
        # (M0/M1 float LOW -> 1/16 step). This greatly reduces the stepper
        # vibration that was disturbing the fiber.
        GPIO.setup(Extruder.MICROSTEP_M2_PIN, GPIO.OUT)
        GPIO.output(Extruder.MICROSTEP_M2_PIN, GPIO.HIGH)
        # PWM Setup
        self.pwm = GPIO.PWM(Extruder.STEP_PIN, 1000)  
        self.pwm.start(0)  
        
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

        With 1/16 microstepping the driver needs MICROSTEP_FACTOR step pulses
        per full motor step, so the pulse frequency is multiplied to keep the
        same physical RPM the user requested.
        """
        full_steps_per_second = (rpm * Extruder.STEPS_PER_REVOLUTION) / 60
        frequency = full_steps_per_second * Extruder.MICROSTEP_FACTOR
        if frequency <= 0:
            return
        self.pwm.ChangeFrequency(frequency)
        self.pwm.ChangeDutyCycle(50)

    def stepper_control_loop(self) -> None:
        """Control stepper motor speed"""
        try:
            setpoint_rpm = self.gui.get_extrusion_speed()
            self.pwm.ChangeDutyCycle(0)
            if setpoint_rpm > 0.0:
                self.set_motor_speed(setpoint_rpm)
            Database.extruder_rpm.append(setpoint_rpm)
        except Exception as e:
            print(f"Error in stepper control loop: {e}")
            self.gui.show_message("Error", "Stepper control loop error")

    def temperature_control_loop(self, current_time: float) -> None:
        """Closed loop control of the temperature of the extruder for desired diameter"""
        if current_time - self.previous_time <= Extruder.SAMPLE_TIME:
            return
        try:
            target_temperature = self.gui.get_target_temperature()
            kp, ki, kd = self.gui.get_temperature_pid()

            delta_time = current_time - self.previous_time
            self.previous_time = current_time
            temperature = Thermistor.get_temperature(self.channel_0.voltage)
            
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
        if current_time - self.previous_time <= Extruder.SAMPLE_TIME:
            return
            
        try:
            pwm_value = self.gui.get_heater_pwm()
            delta_time = current_time - self.previous_time
            self.previous_time = current_time
            temperature = Thermistor.get_temperature(self.channel_0.voltage)

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
        """Stop the extrusion stepper (zero the step PWM)."""
        try:
            self.pwm.ChangeDutyCycle(0)
        except Exception as e:
            print(f"Error stopping stepper: {e}")

    def monitor_temperature(self, current_time: float) -> None:
        """Read and graph the temperature WITHOUT driving the heater.

        Used by the interface's monitor mode to observe how the heater
        temperature behaves on its own, with no control output applied.
        """
        if current_time - self.previous_time <= Extruder.SAMPLE_TIME:
            return
        try:
            delta_time = current_time - self.previous_time
            self.previous_time = current_time
            temperature = Thermistor.get_temperature(self.channel_0.voltage)

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
                 
