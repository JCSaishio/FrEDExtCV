"""Extrusion stepper settings: pins and microstepping (DRV8825 driver).

The single place for the stepper settings. The main program (extruder.py)
and the skipped-step checker (step_check.py / check_stepper.sh) both read
them, so they always drive the motor the same way.

To change the microstep resolution, change MICROSTEPS below - nothing else.
"""

# --------------------------------------------------------------------------- #
# Pins (BCM numbering), as on the MIT FrED PCB 2.2 and later (the same pins
# as mit-fredfactory/fred-device main). On a board older than PCB 2.2 the
# STEP signal is on BCM12 (header pin 32) instead of BCM20.
# --------------------------------------------------------------------------- #
STEP_PIN = 20        # header pin 38
DIRECTION_PIN = 16   # header pin 36
M0_PIN = 17          # header pin 11 -> DRV8825 M0
M1_PIN = 27          # header pin 13 -> DRV8825 M1
M2_PIN = 22          # header pin 15 -> DRV8825 M2

STEPS_PER_REVOLUTION = 200   # full steps per motor turn (1.8 degree motor)

# =========================================================================== #
# MICROSTEPPING - CHANGE THE RESOLUTION HERE
# =========================================================================== #
# MICROSTEPS = STEP pulses per full motor step. The program sets M0/M1/M2
# from the table below and multiplies the step frequency by MICROSTEPS, so the
# Extrusion Motor Speed (RPM) stays correct in every mode.
#
#   MICROSTEPS   M0     M1     M2     DRV8825 mode
#        1       LOW    LOW    LOW    full step (loudest, most vibration)
#        2       HIGH   LOW    LOW    1/2 step
#        4       LOW    HIGH   LOW    1/4 step
#        8       HIGH   HIGH   LOW    1/8 step
#       16       LOW    LOW    HIGH   1/16 step   <- in use
#       32       HIGH   LOW    HIGH   1/32 step
#
# 1/16 only needs M2 HIGH, so it works on boards with all three mode pins
# wired (MIT) and on boards where only M2 reaches the driver (M0/M1 then stay
# LOW through the DRV8825's internal pull-downs). 1/2, 1/4, 1/8 and 1/32 also
# need M0 and/or M1: check with a multimeter that GPIO17 / GPIO27 reach the
# driver's M0 / M1 pins before using them, otherwise the motor turns at the
# wrong speed. After a change, run the skipped-step check (Check Steps) once.
#
# The step pulses come from a software PWM, so keep the pulse rate moderate:
# at the 20 RPM maximum, 1/16 is 1067 pulses/s and 1/32 is 2133 pulses/s.
MICROSTEPS = 16

MODE_PIN_LEVELS = {   # MICROSTEPS -> (M0, M1, M2); 1 = HIGH, 0 = LOW
    1: (0, 0, 0),
    2: (1, 0, 0),
    4: (0, 1, 0),
    8: (1, 1, 0),
    16: (0, 0, 1),
    32: (1, 0, 1),
}


def mode_pin_levels(microsteps: int = MICROSTEPS) -> tuple:
    """(M0, M1, M2) levels for ``microsteps``."""
    try:
        return MODE_PIN_LEVELS[microsteps]
    except KeyError:
        raise ValueError(
            f"MICROSTEPS = {microsteps} is not a DRV8825 mode; use one of "
            f"{sorted(MODE_PIN_LEVELS)}") from None


def pulses_per_revolution(microsteps: int = MICROSTEPS) -> int:
    """STEP pulses for one motor revolution."""
    return STEPS_PER_REVOLUTION * microsteps


def step_frequency(rpm: float, microsteps: int = MICROSTEPS) -> float:
    """STEP pulses per second that turn the motor at ``rpm``."""
    return rpm * pulses_per_revolution(microsteps) / 60.0


def set_mode_pins(GPIO, microsteps: int = MICROSTEPS) -> None:
    """Drive M0/M1/M2 for ``microsteps``.

    All three pins are driven every time, so the resolution is known whatever
    an earlier program left them at (a Pi pin keeps its level until a reboot
    or until a program sets it).
    """
    levels = mode_pin_levels(microsteps)
    for pin, level in zip((M0_PIN, M1_PIN, M2_PIN), levels):
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.HIGH if level else GPIO.LOW)
