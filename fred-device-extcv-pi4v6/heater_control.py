######### Program to control / identify the FrED's extruder HEATER #########
# Adapted from motor_control.py (DC spooler) to the thermal subsystem.
#
# Two modes, chosen from the TERMINAL (see examples below):
#   --mode identify -> OPEN LOOP: apply a known PWM profile and log T(t).
#                      Use this data to fit the FOPDT / ARX model.
#   --mode control  -> CLOSED LOOP: a PID drives the heater to --setpoint
#                      using --kp/--ki/--kd. Use this to validate the gains.
#
# Examples:
#   python heater_control.py --mode identify --profile 2
#   python heater_control.py --mode control  --setpoint 90 --kp 1.0 --ki 0.004 --kd 1.8
#
# !!! SAFETY: a heater can cause fire / burns. Before running you MUST set
#     HEATER_PIN and TEMP_MAX (fixed in this file) to match YOUR wiring and
#     YOUR material. Never leave it unattended.

import time
import math
import argparse
import board
import busio
import digitalio
import RPi.GPIO as GPIO
import matplotlib.pyplot as plt
import adafruit_mcp3xxx.mcp3008 as MCP
from adafruit_mcp3xxx.analog_in import AnalogIn

import FrED_functions
# Shared EMA filter, tuned from the real experiment data (see signal_filter.py).
from signal_filter import EMAFilter, ALPHA_TEMPERATURE, rescale_alpha

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

#######################################################
########## CONFIG: set these before running ###########
#######################################################
HEATER_PIN  = 6          # BCM GPIO 6 — matches Extruder.HEATER_PIN in extruder.py / main.py
HEATER_FREQ = 1          # Hz. Matches the 1 Hz used in extruder.py (slow thermal system).

# ---- Mode is chosen from the TERMINAL, not by editing this file ----
#   python heater_control.py --mode identify --profile 1
#   python heater_control.py --mode control  --setpoint 90
parser = argparse.ArgumentParser(
    description="FrED heater: identify (open loop, for system ID) or control (closed loop PID).")
parser.add_argument("--mode", choices=["identify", "control"], default="identify",
                    help="identify = open loop for system ID; control = closed loop PID. Default: identify")
parser.add_argument("--profile", type=int, default=1,
                    help="open-loop test profile (1, 2 or 3). Only used in identify mode. Default: 1")
parser.add_argument("--setpoint", type=float, default=90.0,
                    help="temperature setpoint in degC. Only used in control mode. Default: 90")
parser.add_argument("--kp", type=float, default=1.0,
                    help="PID proportional gain. Only used in control mode. Default: 1.0")
parser.add_argument("--ki", type=float, default=0.004,
                    help="PID integral gain. Only used in control mode. Default: 0.004")
parser.add_argument("--kd", type=float, default=1.8,
                    help="PID derivative gain. Only used in control mode. Default: 1.8")
args = parser.parse_args()

MODE           = args.mode       # "identify" or "control"
PROFILE        = args.profile    # which open-loop test to run when MODE == "identify"
temp_reference = args.setpoint   # degC setpoint, used ONLY in "control" mode
KP, KI, KD     = args.kp, args.ki, args.kd   # PID gains, used ONLY in control mode

# Human-readable description for each identification profile (used in the banner)
PROFILE_DESC = {
    1: "clean step (escalon limpio)",
    2: "staircase (escalera)",
    3: "step up then down (sube y baja)",
}

########## SAFETY LIMITS (do not skip) ##########
TEMP_MAX          = 220.0  # degC  hard cap -> heater forced OFF above this  <-- SET to your hotend rating
TEMP_SENSOR_MIN   = 1.0    # degC  below this the thermistor is likely disconnected -> force OFF
HEATER_PWM_CEILING = 100.0 # %     optional upper bound on duty cycle during testing

########## sample / timing ##########
tm         = 0.1     # s  sample time. Thermal is slow; 0.1-1.0 s all work well.
match_time = tm

########## temperature EMA filter ##########
# Same TYPE of filter as before (was FrED_functions.temp_filter, an EMA), but the
# coefficient now comes from the recorded experiment data (signal_filter.py).
# ALPHA_TEMPERATURE is tuned for 50 Hz; rescale it to THIS script's sample rate
# (1/tm = 10 Hz here) so the smoothing / time constant is preserved.
temp_ema = EMAFilter(rescale_alpha(ALPHA_TEMPERATURE, 50.0, 1.0 / tm))

#######################################################
########## Thermistor input (MCP3008 ADC) #############
#######################################################
spi = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)
cs  = digitalio.DigitalInOut(board.D8)
mcp = MCP.MCP3008(spi, cs)
thermistor = AnalogIn(mcp, MCP.P0)   # same channel as in motor_control.py

#######################################################
########## Heater PWM output ##########################
#######################################################
GPIO.setup(HEATER_PIN, GPIO.OUT)
heater = GPIO.PWM(HEATER_PIN, HEATER_FREQ)
heater.start(0)   # start OFF

def heater_off():
    """Force the heater fully off. Used on exit and on any safety trip."""
    try:
        heater.ChangeDutyCycle(0)
    except Exception:
        pass

#######################################################
########## Open-loop test profiles (for ID) ###########
#######################################################
def heater_input_profile(current_time, option):
    """Return heater PWM duty (%) as a function of time, for open-loop
    system identification. Keep amplitudes inside a safe band."""
    if option == 1:
        # --- Single positive step: cleanest for FOPDT (K, tau, theta) ---
        # 20 s of OFF to capture ambient, then a step to 45 %.
        output = 0 if current_time < 20 else 45

    elif option == 2:
        # --- Up staircase then down: richer excitation for ARX / least squares ---
        if   current_time < 60:  output = 25
        elif current_time < 120: output = 40
        elif current_time < 180: output = 55
        elif current_time < 240: output = 70
        elif current_time < 360: output = 40
        else:                    output = 25

    elif option == 3:
        # --- Step up then step down: lets you compare heating vs cooling dynamics ---
        if   current_time < 30:  output = 0
        elif current_time < 180: output = 50
        elif current_time < 330: output = 20
        else:                    output = 50

    elif option == 4:
        # --- Up staircase then down: richer excitation for ARX / least squares ---
        if   current_time < 60:  output = 15
        elif current_time < 120: output = 26
        elif current_time < 180: output = 38
        elif current_time < 240: output = 50
        elif current_time < 360: output = 30
        else:                    output = 10


    else:
        output = 0

    return max(min(output, HEATER_PWM_CEILING), 0)

#######################################################
########## PID step (same math as extruder_PID) #######
#######################################################
def pid_step(reference, measurement, prev_error, error_sum,
             current_time, prev_time, kp, ki, kd):
    """One PID iteration. Identical to FrED_functions.extruder_PID, but the
    gains come in as arguments so they can be set from the terminal."""
    delta_time = current_time - prev_time
    error   = reference - measurement
    error_d = (error - prev_error) / delta_time if delta_time > 0 else 0.0
    error_i = (error * delta_time) + error_sum
    output  = kp * error + ki * error_i + kd * error_d
    return output, error_i, error

#######################################################
########## Controller state ###########################
#######################################################
previous_PIDtime      = 0
previous_PIDerror     = 0
error_sum             = 0
last_good_sensor_time = None   # tracks when we last got a valid reading

########## data lists (match save_data_temp signature) ##########
time_data            = []
stepper_rpm_data     = []   # heater-only run: stepper kept at 0 (no filament feed)
temperature_raw_data = []
temperature_data     = []
heater_pwm_data      = []
temp_reference_data  = []

########## live plot ##########
plt.ion()
fig, ax = plt.subplots()
line_T,   = ax.plot(time_data, temperature_data,    label='Temperature (C)')
line_ref, = ax.plot(time_data, temp_reference_data, label='Reference (C)')
ax.legend(); ax.set_xlabel('time (s)'); ax.set_ylabel('Temperature (C)')
ax.set_title('FrED heater - ' + MODE)

def ploting():
    line_T.set_xdata(time_data);   line_T.set_ydata(temperature_data)
    line_ref.set_xdata(time_data); line_ref.set_ydata(temp_reference_data)
    ax.relim(); ax.autoscale_view(); plt.draw(); plt.pause(0.01)

#######################################################
########## Startup banner (reconfirmation) ############
#######################################################
print("=" * 60)
print(" FrED HEATER")
print("=" * 60)
if MODE == "identify":
    desc = PROFILE_DESC.get(PROFILE, "UNKNOWN profile -> heater stays OFF")
    print(f" Mode      : IDENTIFY  (open loop, system identification)")
    print(f" Profile   : {PROFILE}  ->  {desc}")
else:  # control
    print(f" Mode      : CONTROL  (closed loop PID)")
    print(f" Setpoint  : {temp_reference:.1f} C")
    print(f" Gains     : Kp = {KP}   Ki = {KI}   Kd = {KD}")
print(f" Filter    : EMA alpha = {temp_ema.alpha:.3f}  (rescaled to {1.0/tm:.0f} Hz)")
print(f" TEMP_MAX  : {TEMP_MAX:.1f} C  (safety cutoff, fixed in file)")
print(f" Sample    : {tm:.2f} s")
print("=" * 60)
print(" Press Ctrl+C to stop and save data to FrED_data_temp.txt")
print("=" * 60)
print("  time(s)\ttemp(C)\tpwm(%)")   # column header for the data below

#######################################################
########## Main loop ##################################
#######################################################
tstart  = time.perf_counter()
muestra = 1

try:
    while True:
        current_time = time.perf_counter() - tstart

        # 1) read -> convert -> filter temperature (EMA, signal_filter.EMAFilter)
        sensor_ok = False
        try:
            voltage         = thermistor.voltage
            raw_temperature = FrED_functions.get_temperature(voltage)
            temperature     = temp_ema.update(raw_temperature)
            sensor_ok       = True
            last_good_sensor_time = current_time
        except Exception as e:
            print(f"[WARN] {current_time:.1f}s — sensor error: {e}")

        # if no valid reading for > 1 s, force heater OFF
        sensor_timeout = (last_good_sensor_time is None or
                          current_time - last_good_sensor_time > 1.0)
        if not sensor_ok and sensor_timeout:
            print(f"[WARN] {current_time:.1f}s — 1 s without valid sensor data. Heater OFF.")
            heater_off()
            match_time += tm
            continue

        # 2) decide the heater command
        if MODE == "control":
            heater_pwm, error_i, PIDerror = pid_step(
                temp_reference, temperature, previous_PIDerror,
                error_sum, current_time, previous_PIDtime, KP, KI, KD)
            previous_PIDtime  = current_time
            previous_PIDerror = PIDerror
            error_sum         = error_i
            ref_now = temp_reference
        else:  # "identify" -> open loop, ignore the PID
            heater_pwm = heater_input_profile(current_time, PROFILE)
            ref_now    = 0   # no setpoint in open loop

        # 3) clamp to a valid duty cycle
        heater_pwm = max(min(heater_pwm, HEATER_PWM_CEILING), 0)

        # 4) SAFETY: over-temperature or sensor fault -> force OFF
        if temperature >= TEMP_MAX:            # over-temperature (filtered)
            heater_pwm = 0
        if raw_temperature < TEMP_SENSOR_MIN:  # thermistor disconnected / shorted
            heater_pwm = 0

        # 5) apply
        heater.ChangeDutyCycle(heater_pwm)

        # 6) console feedback (time \t temp \t pwm)
        if current_time >= muestra:
            print(f"{current_time:0.1f}\t{temperature:0.2f}\t{heater_pwm:0.1f}")
            muestra += 1

        # 7) store data
        time_data.append(round(current_time, 2))
        stepper_rpm_data.append(0)
        temperature_raw_data.append(round(raw_temperature, 2))
        temperature_data.append(round(temperature, 2))
        heater_pwm_data.append(round(heater_pwm, 2))
        temp_reference_data.append(round(ref_now, 2))

        # ploting()   # uncomment for a live plot (it slows the loop)

        # 8) keep a consistent sample time
        wait = max(0, match_time - current_time)
        time.sleep(wait)
        match_time += tm

except KeyboardInterrupt:
    print("\nCode Stopped\n")
    heater_off()
    FrED_functions.save_data_temp(
        time_data, stepper_rpm_data, temperature_raw_data,
        temperature_data, heater_pwm_data, temp_reference_data)
    print("Data saved in FrED_data_temp.txt\n")

finally:
    heater_off()      # make sure the heater is OFF no matter how we exit
    GPIO.cleanup()
