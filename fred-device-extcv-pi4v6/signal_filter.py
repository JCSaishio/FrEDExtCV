"""Signal filtering for the fast data-acquisition path (headless, ~50 Hz).

Same *type* of filter used in the heater/spooler code
(``FrED_functions.filter`` / ``temp_filter``): a first-order
**exponential moving average** (EMA), a.k.a. one-pole IIR low-pass::

    y[n] = alpha * x[n] + (1 - alpha) * y[n-1]

It costs a single multiply-add per sample, so the CPU budget stays on the
control loop and the acquisition — not on smoothing or plotting. Each sensor
keeps BOTH its raw and its filtered value (mirroring how the spooler code logs
``rpm_raw`` next to ``rpm``), so nothing is lost and the filter can be re-tuned
offline from the recorded raw column.

------------------------------------------------------------------------------
How the coefficients were chosen (statistical basis)
------------------------------------------------------------------------------
Measured on the real experiment exports in
``DatosExperimentos/AntiguaFrecuencia`` (logged ~45 Hz, but the sensors/control
actually update ~10 Hz for temp/RPM and ~2-20 Hz for diameter, so the log
repeats each value ~5x). After collapsing the repeats to the true update
sequence and estimating noise with the von-Neumann successive-difference
estimator  sigma ~ std(diff)/sqrt(2)  (robust to the heating ramp):

    signal            level      noise sigma      relative noise
    Temperature (C)   ~85        ~2.7  C          ~10 %   (mostly the ramp; the
                                                           steady-state noise is
                                                           a few tenths of a deg)
    Spooler RPM       ~29-30     ~1.3-1.7 RPM     ~6 %
    Diameter raw (mm) ~0.4       ~0.010 mm        ~2.4 %

An EMA reduces white-noise std by  sqrt(alpha / (2 - alpha)).  The time
constant is  tau = (1 - alpha)/alpha * dt.  IMPORTANT: alpha is rate-dependent
-- reusing a coefficient tuned at another sample rate changes the smoothing.
These presets are computed for **fs = 50 Hz (dt = 0.02 s)**:

    Spooler RPM   alpha = 0.25  -> tau ~0.06 s, cutoff ~2.3 Hz, keeps ~42% of
                                   the noise std (cuts ~58%). Matches the intent
                                   of motor_control.py (alpha=0.30 @50 Hz);
                                   nudged down because a fresh 50 Hz encoder read
                                   is noisier (fewer counts/sample) than the
                                   logged 10 Hz value.
    Temperature   alpha = 0.03  -> tau ~0.65 s, cutoff ~0.24 Hz, keeps ~12%
                                   (cuts ~88%). Preserves the strong smoothing of
                                   the heater code (temp_filter alpha=0.10 @~10 Hz,
                                   tau~0.9 s) at the new 50 Hz rate. Thermal
                                   dynamics are tens of seconds, so the ~0.65 s
                                   lag is negligible.
    Diameter      alpha = 0.20  -> tau ~0.08 s, cutoff ~1.7 Hz. Light: the
                                   diameter is already median+mean filtered on
                                   arrival (external_diameter.py) and streams at
                                   ~20 Hz, so this only trims residual jitter.

Re-tune any of these from the recorded raw columns with
``alpha_for_std_reduction`` / ``rescale_alpha`` below.
"""
import math

# --- Presets for fs = 50 Hz (see the module docstring for the derivation) --- #
SAMPLE_RATE_HZ = 50.0
ALPHA_TEMPERATURE = 0.03
ALPHA_SPOOLER_RPM = 0.25
ALPHA_DIAMETER = 0.20


class EMAFilter:
    """First-order exponential moving average (one-pole IIR low-pass).

    Identical math to the heater/spooler filters, wrapped so each signal keeps
    its own state and can be reset between runs.

        f = EMAFilter(ALPHA_SPOOLER_RPM)
        rpm_filtered = f.update(rpm_raw)
    """

    __slots__ = ("alpha", "_y", "_primed")

    def __init__(self, alpha: float) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError(f"alpha must be in (0, 1], got {alpha}")
        self.alpha = float(alpha)
        self._y = 0.0
        self._primed = False

    def update(self, x: float) -> float:
        """Feed one raw sample, return the filtered value."""
        if not self._primed:          # seed with the first sample (no startup dip)
            self._y = x
            self._primed = True
        else:
            self._y += self.alpha * (x - self._y)   # = a*x + (1-a)*y_prev
        return self._y

    @property
    def value(self) -> float:
        """Last filtered value (0.0 before the first update)."""
        return self._y

    def reset(self) -> None:
        """Forget the state so the next update re-seeds (use on a new run)."""
        self._y = 0.0
        self._primed = False


# --------------------------------------------------------------------------- #
# Design helpers -- use these to re-tune from the recorded raw data.
# --------------------------------------------------------------------------- #
def alpha_for_std_reduction(reduction: float) -> float:
    """alpha so filtered_noise_std / raw_noise_std == ``reduction`` (white noise).

    e.g. reduction=0.5 halves the noise std. Derived from
    std_out/std_in = sqrt(alpha/(2-alpha)).
    """
    if not 0.0 < reduction <= 1.0:
        raise ValueError("reduction must be in (0, 1]")
    r2 = reduction * reduction
    return 2.0 * r2 / (1.0 + r2)


def time_constant_s(alpha: float, fs: float = SAMPLE_RATE_HZ) -> float:
    """EMA time constant tau (seconds) at sample rate ``fs``."""
    return (1.0 - alpha) / alpha / fs


def cutoff_hz(alpha: float, fs: float = SAMPLE_RATE_HZ) -> float:
    """-3 dB cutoff frequency (Hz) of the EMA at sample rate ``fs``."""
    c = 1.0 - (alpha ** 2) / (2.0 * (1.0 - alpha))
    c = max(min(c, 1.0), -1.0)
    return math.acos(c) * fs / (2.0 * math.pi)


def rescale_alpha(alpha_old: float, fs_old: float, fs_new: float) -> float:
    """Re-derive alpha for a new sample rate, preserving the time constant tau.

    Reuse this if you ever move the acquisition off 50 Hz so the smoothing
    stays the same instead of silently changing.
    """
    tau = time_constant_s(alpha_old, fs_old)
    dt_new = 1.0 / fs_new
    return dt_new / (tau + dt_new)


if __name__ == "__main__":       # quick self-report of the presets
    for name, a in (("Temperature", ALPHA_TEMPERATURE),
                    ("Spooler RPM", ALPHA_SPOOLER_RPM),
                    ("Diameter", ALPHA_DIAMETER)):
        red = math.sqrt(a / (2 - a))
        print(f"{name:12s} alpha={a:.3f}  tau={time_constant_s(a):.3f}s  "
              f"cutoff={cutoff_hz(a):.2f}Hz  keeps {100*red:.0f}% of noise std")
