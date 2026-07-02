#!/bin/bash
#
# setup_install.sh — one-shot installer for the FrED device (external-CV variant)
# Target: Raspberry Pi 4 running Raspberry Pi OS (Bullseye/Bookworm, 32- or 64-bit).
#
# What it does:
#   1. Installs the system (apt) packages that are painful or impossible to build
#      with pip on the Pi 4 — most importantly PyQt5 + the QtSvg module, plus
#      RPi.GPIO and the math libraries matplotlib/numpy rely on.
#   2. Creates a virtual environment called  fred-venv  in this folder, built
#      WITH --system-site-packages so the apt PyQt5/QtSvg/RPi.GPIO are visible.
#   3. Installs the remaining pure-Python / wheel packages from requirements.txt
#      INTO that venv.
#   4. Verifies that every library the program imports can actually be imported.
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
# 1. System packages (apt)
# ---------------------------------------------------------------------------
printf "\n[1/4] Installing system packages with apt (sudo may prompt for your password)...\n"
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
# 2. Virtual environment (fred-venv) with access to the apt packages above
# ---------------------------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
  printf "\n[2/4] Creating virtual environment 'fred-venv' (with system site packages)...\n"
  python3 -m venv --system-site-packages "$VENV_DIR"
else
  printf "\n[2/4] Virtual environment 'fred-venv' already exists - reusing it.\n"
fi

# Activate it for the rest of this script.
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ---------------------------------------------------------------------------
# 3. Python packages (pip) into the venv
# ---------------------------------------------------------------------------
printf "\n[3/4] Installing Python packages into fred-venv...\n"
python -m pip install --upgrade pip wheel
python -m pip install -r "$PROJECT_DIR/requirements.txt"

# ---------------------------------------------------------------------------
# 4. Verify every import the program uses actually works
# ---------------------------------------------------------------------------
printf "\n[4/4] Verifying imports...\n"
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
