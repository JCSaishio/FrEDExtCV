#!/bin/bash
#
# setup_install.sh — one-shot installer for the FrED device (external-CV variant)
# Target: Raspberry Pi 4 running Raspberry Pi OS (Bullseye/Bookworm, 32- or 64-bit).
#
# What it does:
#   1. Synchronizes the system clock. The Pi 4 has no battery-backed clock; if
#      the date is wrong, apt rejects the repository Release files ("is not
#      valid yet") and pip/HTTPS downloads fail certificate checks, so NOTHING
#      installs. NTP is tried first, then the date is taken from a web server's
#      HTTP header as a fallback.
#   2. Installs the system (apt) packages that are painful or impossible to build
#      with pip on the Pi 4 — most importantly PyQt5 + the QtSvg module, plus
#      RPi.GPIO and the math libraries matplotlib/numpy rely on.
#   3. Creates a virtual environment called  fred-venv  in this folder, built
#      WITH --system-site-packages so the apt PyQt5/QtSvg/RPi.GPIO are visible.
#   4. Installs the remaining pure-Python / wheel packages from requirements.txt
#      INTO that venv.
#   5. Verifies that every library the program imports can actually be imported.
#
# Run it from inside this folder:
#     bash setup_install.sh
#
# Afterwards start the program with:
#     source fred-venv/bin/activate
#     python main.py
#   (or just:  bash start_fred.sh )

# Stop on the first error and treat unset vars as errors.
set -euo pipefail

# Always operate relative to the folder this script lives in.
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
VENV_DIR="$PROJECT_DIR/fred-venv"

printf "\n=== FrED device (external CV) - Raspberry Pi 4 installer ===\n"
printf "Project folder: %s\n" "$PROJECT_DIR"

# ---------------------------------------------------------------------------
# 1. Clock sync — the Pi 4 has no battery-backed clock. If the date is wrong,
#    apt refuses the repository Release files ("is not valid yet / not valid
#    until ...") and pip/HTTPS downloads fail certificate checks, so none of
#    the packages below can be downloaded. Fix the clock before anything else.
# ---------------------------------------------------------------------------
printf "\n[1/5] Synchronizing the system clock (needed for the downloads below)...\n"
printf "Clock before sync: %s\n" "$(date)"

clock_synced() {
  timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -qi yes
}

http_header_date() {
  # Read the Date: header of a plain-HTTP response. Plain HTTP is used on
  # purpose: with a badly wrong clock, HTTPS certificate checks fail, but the
  # HTTP Date header still comes through and is accurate to the second.
  url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -fsI --max-time 10 "$url" 2>/dev/null \
      | tr -d '\r' | grep -i '^date:' | head -n1 | cut -d' ' -f2- || true
  elif command -v wget >/dev/null 2>&1; then
    wget -qS -O /dev/null --timeout=10 "$url" 2>&1 \
      | tr -d '\r' | grep -i '^ *date:' | head -n1 | sed 's/^ *[Dd]ate: *//' || true
  fi
}

sync_clock() {
  # Preferred: real NTP through systemd-timesyncd (keeps the clock synced
  # from now on, not just for this install).
  if command -v timedatectl >/dev/null 2>&1; then
    sudo timedatectl set-ntp true 2>/dev/null || true
    for _ in $(seq 1 20); do
      clock_synced && return 0
      sleep 1
    done
  fi
  # Fallback: set the date from a web server's HTTP Date header.
  for url in http://google.com http://deb.debian.org http://archive.raspberrypi.org; do
    header_date="$(http_header_date "$url")"
    if [ -n "$header_date" ]; then
      sudo date -s "$header_date" >/dev/null 2>&1 && return 0
    fi
  done
  return 1
}

if clock_synced; then
  printf "Clock is already NTP-synchronized.\n"
elif sync_clock; then
  printf "Clock synchronized.\n"
else
  printf "WARNING: could not synchronize the clock (no internet route?).\n"
  printf "         If apt/pip fail below with 'Release file ... is not valid yet'\n"
  printf "         or certificate errors, connect the Pi to the internet first\n"
  printf "         (ethernet or a normal WiFi network - NOT the FrED_Pi hotspot,\n"
  printf "         which has no internet; run 'bash setup_hotspot.sh down' to\n"
  printf "         leave hotspot mode) and re-run this installer.\n"
fi
printf "Clock now: %s\n" "$(date)"

# ---------------------------------------------------------------------------
# 2. System packages (apt)
# ---------------------------------------------------------------------------
printf "\n[2/5] Installing system packages with apt (sudo may prompt for your password)...\n"
sudo apt-get update
sudo apt-get install -y \
  python3-venv \
  python3-pip \
  python3-dev \
  python3-pyqt5 \
  python3-pyqt5.qtsvg \
  python3-rpi.gpio \
  libatlas-base-dev \
  fonts-dejavu

# python3-venv .............. lets us create the virtual environment
# python3-pip/dev ........... pip + headers for building any small wheels
# python3-pyqt5 ............. the GUI toolkit (prebuilt for ARM)
# python3-pyqt5.qtsvg ....... QtSvg module matplotlib's Qt backend needs
# python3-rpi.gpio .......... GPIO access, prebuilt
# libatlas-base-dev ......... BLAS runtime numpy/matplotlib link against
# fonts-dejavu .............. fonts so matplotlib labels render

# ---------------------------------------------------------------------------
# 3. Virtual environment (fred-venv) with access to the apt packages above
# ---------------------------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
  printf "\n[3/5] Creating virtual environment 'fred-venv' (with system site packages)...\n"
  python3 -m venv --system-site-packages "$VENV_DIR"
else
  printf "\n[3/5] Virtual environment 'fred-venv' already exists - reusing it.\n"
fi

# Activate it for the rest of this script.
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ---------------------------------------------------------------------------
# 4. Python packages (pip) into the venv
# ---------------------------------------------------------------------------
printf "\n[4/5] Installing Python packages into fred-venv...\n"
python -m pip install --upgrade pip wheel
python -m pip install -r "$PROJECT_DIR/requirements.txt"

# ---------------------------------------------------------------------------
# 5. Verify every import the program uses actually works
# ---------------------------------------------------------------------------
printf "\n[5/5] Verifying imports...\n"
python - <<'PYCHECK'
import importlib, sys

checks = [
    ("PyQt5.QtWidgets",          "PyQt5 (GUI)"),
    ("PyQt5.QtSvg",              "PyQt5 QtSvg module"),
    ("matplotlib",               "matplotlib"),
    ("matplotlib.backends.backend_qt5agg", "matplotlib Qt5 backend"),
    ("numpy",                    "numpy"),
    ("socket",                   "socket (WiFi link, stdlib)"),
    ("yaml",                     "PyYAML"),
    ("RPi.GPIO",                 "RPi.GPIO"),
    ("spidev",                   "spidev"),
    ("board",                    "Adafruit Blinka (board)"),
    ("busio",                    "Adafruit Blinka (busio)"),
    ("digitalio",                "Adafruit Blinka (digitalio)"),
    ("adafruit_mcp3xxx.mcp3008", "Adafruit MCP3xxx"),
]

failed = []
for module, label in checks:
    try:
        importlib.import_module(module)
        print(f"  OK   {label}")
    except Exception as exc:                      # noqa: BLE001
        print(f"  FAIL {label}  ->  {exc}")
        failed.append(label)

if failed:
    print("\nSome libraries failed to import: " + ", ".join(failed))
    sys.exit(1)
print("\nAll required libraries import successfully.")
PYCHECK

printf "\n=== Done. ===\n"
printf "To run the program:\n"
printf "    source fred-venv/bin/activate\n"
printf "    python main.py\n"
printf "  (or simply:  bash start_fred.sh )\n\n"
