#!/usr/bin/env python3
"""step_check.py -- skipped-step checker for the extrusion stepper.

FrED has no sensor on the extrusion motor, so no program can see a skipped
step by itself. This check makes one visible: it sends an EXACT number of
step pulses (a whole number of motor revolutions) and you watch a mark on the
shaft. If the mark ends exactly where it started, the motor executed every
step. If it ends short, the motor skipped steps. A stalling stepper loses
steps in groups of 4 full steps (7.2 degrees), so even one slip is easy to
see.

The normal program drives STEP with RPi.GPIO's software PWM, which cannot
count pulses. Here the pulses are sent one by one and counted, on an absolute
schedule so the average speed is exact. A pulse that goes out late is never
followed by a catch-up burst (a burst could itself make the motor skip): the
motor just pauses for a moment, and the late pulses are reported as "Pi
timing".

Two ways to use it:
  * Pi screen (main.py): Extrusion Motor -> Check Steps runs one check at the
    current Extrusion Motor Speed, e.g. with the barrel hot (real load).
  * Terminal (this file): a speed sweep, with main.py CLOSED:
        bash check_stepper.sh                         # 1, 2, 5, 10, 15, 20 RPM
        bash check_stepper.sh --speeds 1.5 3 --revs 2
        python step_check.py --sim                    # dry run off the Pi

Pins and microstepping come from stepper_config.py, like in main.py.
"""
import argparse
import os
import sys
import threading
import time

import stepper_config
from stepper_config import (STEP_PIN, DIRECTION_PIN, MICROSTEPS,
                            pulses_per_revolution, step_frequency)

HEATER_PIN = 6            # Extruder.HEATER_PIN: forced OFF by the terminal tool
PULSE_WIDTH_S = 20e-6     # STEP high time (the DRV8825 needs >= 1.9 us)

SKIP_ADVICE = (
    "If the mark ends short, the motor skipped steps. A stall loses 4 full "
    "steps (7.2 degrees) at a time. Usual causes: the speed is too high for "
    "the load (try a lower RPM), the driver current is too low (raise Vref a "
    "little), cold or stiff plastic in the barrel (heat it first), or the "
    "screw binding mechanically.")


class StepRun:
    """One exact-count run: a whole number of revolutions at a given RPM."""

    def __init__(self, revolutions: float, rpm: float,
                 microsteps: int = MICROSTEPS) -> None:
        self.revolutions = revolutions
        self.rpm = rpm
        self.microsteps = microsteps
        self.requested = round(revolutions * pulses_per_revolution(microsteps))
        self.frequency = step_frequency(rpm, microsteps)
        self.sent = 0
        self.duration = 0.0
        self.late_pulses = 0    # pulses sent more than half a period late
        self.max_late = 0.0     # s
        self.aborted = False

    @property
    def expected_duration(self) -> float:
        return self.requested / self.frequency

    def send(self, GPIO, abort_event: threading.Event = None,
             step_pin: int = STEP_PIN) -> "StepRun":
        """Send the pulses (blocks until done or aborted)."""
        period = 1.0 / self.frequency
        late_limit = 0.5 * period
        start = time.perf_counter()
        due = start
        for _ in range(self.requested):
            if abort_event is not None and abort_event.is_set():
                self.aborted = True
                break
            wait = due - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            now = time.perf_counter()
            late = now - due
            if late > late_limit:
                self.late_pulses += 1
                self.max_late = max(self.max_late, late)
                due = now       # no catch-up burst
            GPIO.output(step_pin, GPIO.HIGH)
            _hold(PULSE_WIDTH_S)
            GPIO.output(step_pin, GPIO.LOW)
            self.sent += 1
            due += period
        self.duration = time.perf_counter() - start
        return self

    def summary(self) -> str:
        """What was sent, e.g. for a message box."""
        mode = "full step" if self.microsteps == 1 else f"1/{self.microsteps} step"
        if self.aborted:
            return (f"Aborted after {self.sent} of {self.requested} steps "
                    f"({self.sent / pulses_per_revolution(self.microsteps):.2f}"
                    f" rev) at {self.rpm:g} RPM.")
        return (f"Sent exactly {self.sent} steps = {self.revolutions:g} "
                f"revolution(s) ({mode}) at {self.rpm:g} RPM in "
                f"{self.duration:.1f} s.")

    def timing_text(self) -> str:
        if self.late_pulses == 0:
            return "Pi timing: every pulse on time."
        return (f"Pi timing: {self.late_pulses} pulse(s) went out more than "
                f"half a step period late (worst {self.max_late * 1000:.1f} ms)"
                f" - the Pi was busy for a moment. The motor only paused "
                f"briefly (never sped up to catch up), so this alone does not "
                f"skip steps.")


class StepCheck(threading.Thread):
    """A StepRun in a background thread (used by main.py's Check Steps).

    ``start_delay`` is waited before the first pulse: main.py stops its
    software PWM thread to free the STEP pin, and that thread lets go of the
    pin within one PWM period.
    """

    def __init__(self, GPIO, revolutions: float, rpm: float,
                 start_delay: float = 0.0,
                 microsteps: int = MICROSTEPS) -> None:
        super().__init__(daemon=True, name="StepCheck")
        self.GPIO = GPIO
        self.start_delay = start_delay
        self.abort_event = threading.Event()
        self.result = StepRun(revolutions, rpm, microsteps)

    def run(self) -> None:
        if self.abort_event.wait(self.start_delay):
            self.result.aborted = True
            return
        self.result.send(self.GPIO, self.abort_event)

    def abort(self) -> None:
        self.abort_event.set()


def _hold(seconds: float) -> None:
    """Busy-wait a few microseconds (time.sleep is far too coarse)."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass


# =========================================================================== #
# Terminal tool
# =========================================================================== #
DEFAULT_SPEEDS = (1.0, 2.0, 5.0, 10.0, 15.0, 20.0)   # RPM (the Pi box allows 0-20)

# Programs that drive the same pins. Closing main.py's window does NOT end the
# program (its hardware thread keeps running), so it is looked for here.
FRED_PROGRAMS = ("main.py", "fred_terminal.py", "motor_control.py",
                 "heater_control.py")

RESET = "\033[0m"; BOLD = "\033[1m"
RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; CYAN = "\033[36m"
GREY = "\033[90m"


def _c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}" if color else text


def enable_ansi() -> None:
    """Turn on ANSI escape processing on Windows terminals (no-op elsewhere)."""
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            pass


class SimGPIO:
    """Stand-in for RPi.GPIO for a dry run off the Pi (--sim)."""
    BCM = "BCM"
    OUT = "OUT"
    HIGH = 1
    LOW = 0

    def __init__(self) -> None:
        self.levels = {}
        self.step_pulses = 0

    def setmode(self, mode) -> None:
        pass

    def setwarnings(self, flag) -> None:
        pass

    def setup(self, pin, mode) -> None:
        self.levels[pin] = 0

    def output(self, pin, level) -> None:
        if pin == STEP_PIN and level and not self.levels.get(pin):
            self.step_pulses += 1
        self.levels[pin] = level


def running_fred_programs() -> list:
    """(pid, command line) of running FrED programs that drive the pins."""
    found = []
    if not os.path.isdir("/proc"):
        return found
    me = os.getpid()
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                args = [a.decode(errors="replace")
                        for a in f.read().split(b"\0") if a]
        except OSError:
            continue
        if not args or "python" not in os.path.basename(args[0]):
            continue
        if any(os.path.basename(a) in FRED_PROGRAMS for a in args[1:]):
            found.append((int(entry), " ".join(args)))
    return found


def ask(prompt: str, choices: str, default: str = "") -> str:
    """Read one of the single-letter ``choices`` (Enter = ``default``)."""
    while True:
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            return "q"
        if not answer and default:
            return default
        if answer[:1] in choices:
            return answer[:1]
        print(f"  Please type one of: {', '.join(choices)}")


def ask_degrees(prompt: str):
    """A number of degrees, or None (Enter). Accepts a decimal comma."""
    while True:
        try:
            answer = input(prompt).strip().replace(",", ".")
        except EOFError:
            return None
        if not answer:
            return None
        try:
            return abs(float(answer))
        except ValueError:
            print("  Please type a number (or just Enter).")


def run_with_progress(GPIO, revolutions: float, rpm: float,
                      microsteps: int) -> StepRun:
    """Run one check in a thread, showing progress; Ctrl+C aborts it."""
    check = StepCheck(GPIO, revolutions, rpm, microsteps=microsteps)
    check.start()
    run = check.result
    try:
        while check.is_alive():
            check.join(0.25)
            print(f"\r  running: {run.sent}/{run.requested} steps "
                  f"({100.0 * run.sent / run.requested:.0f} %)   ",
                  end="", flush=True)
    except KeyboardInterrupt:
        check.abort()
        check.join()
    print()
    return run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Skipped-step check for the FrED extrusion stepper: "
                    "exact step counts, you check a mark on the shaft.")
    parser.add_argument("--speeds", type=float, nargs="+",
                        default=list(DEFAULT_SPEEDS), metavar="RPM",
                        help="speeds to test; they run slowest first "
                             "(default: 1 2 5 10 15 20)")
    parser.add_argument("--revs", type=int, default=1,
                        help="whole revolutions per test (default 1)")
    parser.add_argument("--microsteps", type=int, default=MICROSTEPS,
                        help="microstep resolution to test (default: "
                             "stepper_config.MICROSTEPS = %(default)s)")
    parser.add_argument("--reverse", action="store_true",
                        help="turn the other way (retract)")
    parser.add_argument("--force", action="store_true",
                        help="run even if another FrED program seems to be "
                             "running")
    parser.add_argument("--sim", action="store_true",
                        help="dry run without the Pi's GPIO")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)

    global RESET, BOLD, RED, GREEN, YELLOW, CYAN, GREY
    if args.no_color:
        RESET = BOLD = RED = GREEN = YELLOW = CYAN = GREY = ""
    enable_ansi()

    try:
        stepper_config.mode_pin_levels(args.microsteps)
    except ValueError as exc:
        print(_c(str(exc), RED))
        return 2
    if args.revs < 1:
        print(_c("--revs must be a whole number >= 1 (the mark only lines "
                 "up after whole revolutions).", RED))
        return 2
    speeds = sorted(s for s in args.speeds if s > 0)
    if not speeds:
        print(_c("Give at least one speed above 0 RPM.", RED))
        return 2

    per_rev = pulses_per_revolution(args.microsteps)
    mode = "full step" if args.microsteps == 1 else f"1/{args.microsteps} step"
    print(_c("\n=== FrED extrusion stepper - skipped-step check ===", BOLD))
    print(f"STEP BCM{STEP_PIN}, DIR BCM{DIRECTION_PIN}, M0/M1/M2 BCM"
          f"{stepper_config.M0_PIN}/{stepper_config.M1_PIN}/"
          f"{stepper_config.M2_PIN} | {mode}, {per_rev} steps per revolution"
          f" | {args.revs} revolution(s) per test | "
          f"{'reverse' if args.reverse else 'extrusion direction'}")

    if args.sim:
        GPIO = SimGPIO()
        print(_c("SIMULATION: no GPIO is driven.", YELLOW))
    else:
        others = running_fred_programs()
        if others and not args.force:
            print(_c("\nAnother FrED program is running and drives the same "
                     "pins:", RED))
            for pid, cmd in others:
                print(f"  PID {pid}: {cmd}")
            print("Stop it first. Closing main.py's window does NOT end the "
                  "program; stop it with\n    pkill -f main.py\n"
                  "(or Ctrl+C in its terminal), then run this again. To test "
                  "under load with the\nbarrel hot, use the Check Steps "
                  "button in main.py instead.")
            return 1
        try:
            import RPi.GPIO as GPIO
        except ImportError:
            print(_c("RPi.GPIO is not available: run this on the Pi (or use "
                     "--sim).", RED))
            return 1

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    # A FrED program that was killed leaves its pins at their last level, so
    # the heater could be latched ON: force it OFF.
    GPIO.setup(HEATER_PIN, GPIO.OUT)
    GPIO.output(HEATER_PIN, GPIO.LOW)
    GPIO.setup(STEP_PIN, GPIO.OUT)
    GPIO.output(STEP_PIN, GPIO.LOW)
    GPIO.setup(DIRECTION_PIN, GPIO.OUT)
    # extruder.py extrudes with set_motor_direction(False) -> DIR HIGH.
    GPIO.output(DIRECTION_PIN, GPIO.LOW if args.reverse else GPIO.HIGH)
    stepper_config.set_mode_pins(GPIO, args.microsteps)
    print(_c(f"Heater output forced OFF (BCM{HEATER_PIN} LOW).", GREY))

    print("\nHow it works: each test sends EXACTLY "
          f"{args.revs * per_rev} steps = {args.revs} revolution(s).")
    print("  1. Put a mark on the motor shaft or coupling (a tape flag "
          "works well)\n     and note what it points at.")
    print("  2. Run a test and watch the motor.")
    print("  3. The mark must end exactly where it started. Short = "
          "skipped steps.")
    print(_c("  With main.py closed the barrel is cold: test with the motor "
             "decoupled or the\n  barrel empty. For a test under real load, "
             "use Check Steps in main.py.", GREY))

    results = []   # (rpm, StepRun, verdict text)
    first_skip = None
    i = 0
    try:
        while i < len(speeds):
            rpm = speeds[i]
            run = StepRun(args.revs, rpm, args.microsteps)
            choice = ask(
                _c(f"\n{rpm:g} RPM", BOLD) +
                f": {run.requested} steps, about {run.expected_duration:.0f}"
                f" s. Enter = run, s = skip, q = finish: ", "rsq", "r")
            if choice == "q":
                break
            if choice == "s":
                i += 1
                continue
            run = run_with_progress(GPIO, args.revs, rpm, args.microsteps)
            print("  " + run.summary())
            print("  " + _c(run.timing_text(),
                            GREEN if run.late_pulses == 0 else YELLOW))
            if run.aborted:
                results.append((rpm, run, "aborted"))
                if ask("  Aborted. Continue with the next speed? [y/N]: ",
                       "yn", "n") != "y":
                    break
                i += 1
                continue
            answer = ask("  Is the mark exactly back where it started? "
                         "[y]es / [n]o / [r]epeat: ", "ynrq")
            if answer == "q":
                break
            if answer == "r":
                print("  Line the mark up again.")
                continue
            if answer == "y":
                results.append((rpm, run, "OK"))
                i += 1
                continue
            degrees = ask_degrees("  Roughly how many degrees off? "
                                  "(Enter to skip): ")
            verdict = "SKIPPED"
            if degrees is not None:
                full_steps = degrees / (360.0 / stepper_config
                                        .STEPS_PER_REVOLUTION)
                verdict = (f"SKIPPED ~{degrees:g} deg = ~{full_steps:.0f} "
                           f"full steps")
            results.append((rpm, run, verdict))
            if first_skip is None:
                first_skip = rpm
            if ask("  Try the faster speeds too? [y/N]: ", "yn", "n") != "y":
                break
            i += 1
    finally:
        GPIO.output(STEP_PIN, GPIO.LOW)

    if not results:
        print("\nNo test was run.")
        return 0
    print(_c(f"\n=== Summary ({mode}, {args.revs} revolution(s) per test) ===",
             BOLD))
    print(f"  {'RPM':>6}  {'steps sent':>10}  {'time':>7}  {'late pulses':>11}"
          f"  mark")
    for rpm, run, verdict in results:
        color = GREEN if verdict == "OK" else (
            GREY if verdict == "aborted" else RED)
        print(f"  {rpm:>6g}  {run.sent:>10}  {run.duration:>6.1f}s  "
              f"{run.late_pulses:>11}  " + _c(verdict, color))
    clean = [rpm for rpm, _, verdict in results if verdict == "OK"
             and (first_skip is None or rpm < first_skip)]
    if first_skip is None and clean:
        print(_c(f"\nNo skipped steps up to {max(clean):g} RPM.", GREEN))
    elif first_skip is not None:
        best = (f"fastest clean speed {max(clean):g} RPM" if clean
                else "no clean speed below it")
        print(_c(f"\nSkipped steps from {first_skip:g} RPM; {best}.", RED))
        print(SKIP_ADVICE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
